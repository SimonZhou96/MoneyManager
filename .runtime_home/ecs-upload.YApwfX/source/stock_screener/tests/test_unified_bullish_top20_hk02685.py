#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 unified_bullish_top20 规则链对 HK.02685（量化派）的执行情况。

所有依赖均为真实：MySQL 数据库、DB 中的 rule metadata、DB 缓存的 K 线数据、
真实的 RuleEngine + RuleRegistry + Strategizer。
"""

import os
import sys
import unittest
from datetime import date

# 确保能正确导入 stock_screener 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import MarketDatabase, MySqlConfig
from filters import FilterContext, FilterResult, StockInfo
from rule_engine import (
    RuleEngine,
    RuleMetadata,
    RuleRegistry,
    RuleRepository,
    SIGNAL_GROUP_BULLISH,
    SIGNAL_GROUP_REBOUND,
    SIGNAL_GROUP_ZUOYI_BULLISH,
)

# ═══════════════════════════════════════════════════════════════════
# 配置：从 .env 中读取 MySQL 连接信息
# ═══════════════════════════════════════════════════════════════════

MYSQL_CONFIG = MySqlConfig(
    host=os.getenv("MYSQL_HOST", "127.0.0.1"),
    port=int(os.getenv("MYSQL_PORT", "3306")),
    user=os.getenv("MYSQL_USER", "root"),
    password=os.getenv("MYSQL_PASSWORD", "123456"),
    database=os.getenv("MYSQL_DATABASE", "market_data"),
)

STOCK_CODE = "HK.02685"
STOCK_NAME = "量化派"
CHAIN_KEY = "unified_bullish_top20"
TIMEFRAME = "1d"
MARKET = "HK"


# ═══════════════════════════════════════════════════════════════════
# 测试类
# ═══════════════════════════════════════════════════════════════════


class UnifiedBullishTop20HK02685RealDBTest(unittest.TestCase):
    """使用真实 DB + 真实 K 线数据，验证 HK.02685 在 unified_bullish_top20 链的表现。"""

    @classmethod
    def setUpClass(cls):
        # ── 1. 连接数据库，加载真实元数据 ──
        cls.db = MarketDatabase(MYSQL_CONFIG)
        cls.db.init_schema(TIMEFRAME)

        repository = RuleRepository(cls.db)
        cls.all_metadata = repository.load_metadata(MARKET)
        cls.chain_config = repository.load_chain(MARKET, CHAIN_KEY, TIMEFRAME)

        # ── 2. 从 DB 缓存加载 HK.02685 的真实 K 线 ──
        cls.kline_df = cls.db.get_kline_cache(MARKET, STOCK_CODE, TIMEFRAME)
        cls.kline_available = cls.kline_df is not None and not cls.kline_df.empty

        # ── 3. 构建 RuleEngine ──
        cls.engine = RuleEngine(
            metadata=cls.all_metadata,
            chain_config=cls.chain_config,
            registry=RuleRegistry.default(),
        )

        # ── 4. 构建 StockInfo ──
        stock_base = {"code": STOCK_CODE, "name": STOCK_NAME}
        try:
            stocks = cls.db.get_stocks(MARKET, include_fundamentals=True)
            for s in stocks:
                if s.get("code") == STOCK_CODE:
                    stock_base = s
                    break
        except Exception:
            pass

        cls.stock = StockInfo(
            market=MARKET,
            code=STOCK_CODE,
            name=stock_base.get("name") or STOCK_NAME,
            sector=stock_base.get("sector"),
            industry=stock_base.get("industry"),
            market_cap=stock_base.get("market_cap"),
            pe_ratio=stock_base.get("pe_ratio"),
            pb_ratio=stock_base.get("pb_ratio"),
            kline_df=cls.kline_df if cls.kline_available else None,
        )

        # ── 5. 执行 evaluate_bullish_technical_rules ──
        cls.context = FilterContext(check_date=date.today(), market=MARKET)
        cls.result = cls.engine.evaluate_bullish_technical_rules(
            cls.stock, cls.context
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    # ── DB 连通性 & 数据完整性 ─────────────────────────────────

    def test_01_db_connected(self):
        """数据库应可连接并返回规则元数据。"""
        self.assertGreater(
            len(self.all_metadata), 0, "DB 中应至少有一条规则元数据"
        )

    def test_02_chain_config_loaded(self):
        """unified_bullish_top20 链配置应成功加载。"""
        self.assertEqual(self.chain_config.chain_key, CHAIN_KEY)
        self.assertEqual(self.chain_config.market, MARKET)

    def test_03_kline_data_available(self):
        """HK.02685 应有 DB 缓存的 K 线数据。"""
        self.assertTrue(
            self.kline_available,
            f"HK.02685 缺少 DB 缓存的 K 线数据（timeframe={TIMEFRAME}），"
            f"请确保已通过 Futu 或其他数据源预取过",
        )
        if self.kline_available:
            print(
                f"\n  ✓ K 线: {len(self.kline_df)} 根, "
                f"日期 {self.kline_df['date'].min().strftime('%Y-%m-%d')} ~ "
                f"{self.kline_df['date'].max().strftime('%Y-%m-%d')}"
            )

    # ── 规则过滤 ────────────────────────────────────────────────

    def test_04_bullish_technical_rule_count_is_21(self):
        """bullish_technical_rule_keys() 应返回 21 条规则（移除 rsi_oversold 后）。"""
        keys = self.engine.bullish_technical_rule_keys()
        self.assertEqual(
            len(keys), 21,
            f"预期 21 条，实际 {len(keys)}: {sorted(keys)}",
        )

    def test_05_macro_rules_not_in_bullish_technical_keys(self):
        """宏观规则不应出现在 bullish_technical_rule_keys() 中。"""
        keys = set(self.engine.bullish_technical_rule_keys())
        macro_rules = {
            "company_event_hot_sector_link",
            "company_event_hot_news_link",
            "market_intel_macro_score_link",
            "macro_factor_analysis",
            "enterprise_potential_analysis",
        }
        overlap = keys & macro_rules
        self.assertEqual(
            overlap, set(),
            f"bullish_technical_rule_keys 不应包含宏观规则: {overlap}",
        )

    def test_06_filter_rules_not_in_bullish_technical_keys(self):
        """filter 类规则不应出现在 bullish_technical_rule_keys() 中。"""
        keys = set(self.engine.bullish_technical_rule_keys())
        filter_rules = {
            "market_cap_range", "avg_daily_volume_range",
            "price_range", "pe_range", "profitability",
        }
        overlap = keys & filter_rules
        self.assertEqual(
            overlap, set(),
            f"bullish_technical_rule_keys 不应包含 filter 规则: {overlap}",
        )

    def test_07_zuoyi_signal_not_in_keys(self):
        """zuoyi_signal（无 direction 字段）不应出现在列表中。"""
        keys = set(self.engine.bullish_technical_rule_keys())
        self.assertNotIn("zuoyi_signal", keys)

    # ── 门面方法 ────────────────────────────────────────────────

    def test_08_requires_kline_returns_true(self):
        self.assertTrue(self.engine.requires_kline())

    def test_09_requires_signal_analysis_returns_false(self):
        self.assertFalse(self.engine.requires_signal_analysis())

    def test_10_requires_market_intel_macro_score_returns_false(self):
        self.assertFalse(self.engine.requires_market_intel_macro_score())

    def test_11_requires_enterprise_potential_returns_false(self):
        self.assertFalse(self.engine.requires_enterprise_potential())

    def test_12_requires_macro_analysis_returns_false(self):
        self.assertFalse(self.engine.requires_macro_analysis())

    # ── 评估结果：规则覆盖 ──────────────────────────────────────

    def test_13_all_21_rules_executed(self):
        """21 条规则全部被执行，每个 rule_key 在 filter_outputs 中出现恰好一次。"""
        executed = {}
        for o in self.result.filter_outputs:
            details = o.details if isinstance(o.details, dict) else {}
            rk = details.get("rule_key")
            if rk:
                executed[rk] = executed.get(rk, 0) + 1

        expected = set(self.engine.bullish_technical_rule_keys())
        self.assertEqual(
            set(executed.keys()), expected,
            "filter_outputs 中的 rule_key 集合应与 bullish_technical_rule_keys 完全一致",
        )
        for rk, count in executed.items():
            self.assertEqual(count, 1, f"规则 '{rk}' 被执行了 {count} 次，应为 1 次")

    def test_14_no_macro_executed(self):
        """不应执行任何宏观规则。"""
        for o in self.result.filter_outputs:
            details = o.details if isinstance(o.details, dict) else {}
            rk = details.get("rule_key", "")
            self.assertNotIn(
                rk,
                {"macro_factor_analysis", "enterprise_potential_analysis",
                 "company_event_hot_sector_link", "company_event_hot_news_link",
                 "market_intel_macro_score_link"},
                f"不应执行宏观规则 '{rk}'",
            )

    def test_15_no_filter_executed(self):
        """不应执行任何 filter 规则。"""
        for o in self.result.filter_outputs:
            details = o.details if isinstance(o.details, dict) else {}
            rk = details.get("rule_key", "")
            self.assertNotIn(
                rk,
                {"market_cap_range", "avg_daily_volume_range",
                 "price_range", "pe_range", "profitability"},
                f"不应执行 filter 规则 '{rk}'",
            )

    # ── 评估结果：命中统计 ──────────────────────────────────────

    def test_16_match_count_consistency(self):
        """total = bullish + rebound + zuoyi_bullish。"""
        total = self.result.total_match_count
        parts = (
            self.result.bullish_match_count
            + self.result.rebound_match_count
            + self.result.zuoyi_bullish_match_count
        )
        self.assertEqual(total, parts)

    def test_17_categorized_matches_match_outputs(self):
        """categorized_condition_matches 数量应与 PASS 的输出一致。"""
        pass_count = sum(
            1 for o in self.result.filter_outputs
            if o.result == FilterResult.PASS
        )
        cat_count = len(self.result.categorized_condition_matches)
        self.assertEqual(
            cat_count, pass_count,
            f"categorized_condition_matches ({cat_count}) 应与 PASS 输出 ({pass_count}) 一致",
        )

    def test_18_bullish_condition_labels_count(self):
        """bullish_condition_labels 数量应与 total_match_count 一致。"""
        self.assertEqual(
            len(self.result.bullish_condition_labels),
            self.result.total_match_count,
        )

    def test_19_passed_reflects_hits(self):
        """有命中 → passed=True；无命中 → passed=False。"""
        if self.result.total_match_count > 0:
            self.assertTrue(self.result.passed)
        else:
            self.assertFalse(self.result.passed)

    # ── 评估结果：详情展示 ──────────────────────────────────────

    def test_20_print_all_rule_results(self):
        """打印所有 21 条规则的执行结果（方便人工核查）。"""
        print(f"\n{'='*80}")
        print(f"📊 HK.02685 ({STOCK_NAME}) 在 unified_bullish_top20 链的评估结果")
        print(f"   日期: {date.today()}")
        if self.kline_available:
            last_row = self.kline_df.iloc[-1]
            prev_row = self.kline_df.iloc[-2]
            pct = (last_row["close"] - prev_row["close"]) / prev_row["close"] * 100
            print(
                f"   K线末2日: {prev_row['date'].strftime('%m-%d')} close={prev_row['close']:.2f} → "
                f"{last_row['date'].strftime('%m-%d')} close={last_row['close']:.2f} "
                f"({pct:+.2f}%) vol={last_row['volume']:,.0f}"
            )

        print(f"\n   总命中: {self.result.total_match_count} "
              f"(看涨 {self.result.bullish_match_count} / "
              f"准备反弹 {self.result.rebound_match_count} / "
              f"左一看涨 {self.result.zuoyi_bullish_match_count})")
        print(f"   passed: {self.result.passed}")

        # 按 signal_group 分组打印
        groups = {"bullish": [], "rebound": [], "zuoyi_bullish": []}
        for o in self.result.filter_outputs:
            details = o.details if isinstance(o.details, dict) else {}
            rk = details.get("rule_key", "?")
            sg = details.get("signal_group", "bullish")
            icon = {"pass": "✅", "fail": "❌", "skip": "⊝", "error": "⚠️"}.get(
                o.result.value, "?"
            )
            groups.setdefault(sg, []).append((icon, rk, o.result.value, o.reason[:80]))

        group_labels = {"bullish": "看涨", "rebound": "准备反弹", "zuoyi_bullish": "左一看涨"}
        for sg in ("bullish", "rebound", "zuoyi_bullish"):
            items = groups.get(sg, [])
            label = group_labels.get(sg, sg)
            print(f"\n   ── {label} ({len(items)} 条) ──")
            for icon, rk, result, reason in items:
                print(f"     {icon} {rk}: {result} | {reason}")

        print(f"\n   命中的条件标签 ({len(self.result.bullish_condition_labels)}):")
        for label in self.result.bullish_condition_labels:
            print(f"     • {label}")

        print(f"{'='*80}\n")

    def test_21_macro_rules_verified_in_db_but_excluded_from_chain(self):
        """确认 DB 中存在宏观规则，但 unified_bullish_top20 链确实不引用它们。"""
        all_rule_keys = {m.rule_key for m in self.all_metadata}
        macro_in_db = {
            "company_event_hot_sector_link",
            "company_event_hot_news_link",
            "market_intel_macro_score_link",
            "macro_factor_analysis",
            "enterprise_potential_analysis",
        }
        in_db = macro_in_db & all_rule_keys
        self.assertEqual(
            len(in_db), 5,
            f"DB 中应有 5 条宏观规则，实际找到: {in_db}",
        )
        # 链表达式只引用了 ema_breakout
        self.assertEqual(
            self.chain_config.expression, {"ref": "ema_breakout"},
            "unified_bullish_top20 的表达式应为 {'ref': 'ema_breakout'}",
        )
        # 因此 referenced_rule_keys 只有 ema_breakout
        self.assertEqual(
            self.engine.referenced_rule_keys, {"ema_breakout"},
            "referenced_rule_keys 应只包含 ema_breakout",
        )


class UnifiedBullishTop20MacroRulesForTop20Test(unittest.TestCase):
    """验证 evaluate_macro_rules_for_top20() 的正确行为。"""

    @classmethod
    def setUpClass(cls):
        cls.db = MarketDatabase(MYSQL_CONFIG)
        cls.db.init_schema(TIMEFRAME)
        repository = RuleRepository(cls.db)
        cls.all_metadata = repository.load_metadata(MARKET)
        cls.chain_config = repository.load_chain(MARKET, CHAIN_KEY, TIMEFRAME)
        cls.kline_df = cls.db.get_kline_cache(MARKET, STOCK_CODE, TIMEFRAME)

        cls.engine = RuleEngine(
            metadata=cls.all_metadata,
            chain_config=cls.chain_config,
            registry=RuleRegistry.default(),
        )

        cls.stock = StockInfo(
            market=MARKET,
            code=STOCK_CODE,
            name=STOCK_NAME,
            kline_df=cls.kline_df if cls.kline_df is not None and not cls.kline_df.empty else None,
        )
        cls.context = FilterContext(check_date=date.today(), market=MARKET)

        # 先执行技术规则
        cls.tech_result = cls.engine.evaluate_bullish_technical_rules(
            cls.stock, cls.context
        )
        # 再执行宏观规则（模拟 Top20 后置）
        cls.macro_result = cls.engine.evaluate_macro_rules_for_top20(
            cls.stock, cls.context
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_22_macro_rules_execute_exactly_4(self):
        """evaluate_macro_rules_for_top20 应恰好执行 4 条规则。"""
        executed_keys = set()
        for o in self.macro_result.filter_outputs:
            details = o.details if isinstance(o.details, dict) else {}
            rk = details.get("rule_key")
            if rk:
                executed_keys.add(rk)

        expected = {
            "macro_factor_analysis",
            "enterprise_potential_analysis",
            "company_event_hot_sector_link",
            "company_event_hot_news_link",
        }
        self.assertEqual(
            executed_keys, expected,
            f"evaluate_macro_rules_for_top20 应执行 4 条宏观规则，实际: {executed_keys}",
        )

    def test_23_macro_rules_not_in_technical_results(self):
        """宏观规则不应出现在 evaluate_bullish_technical_rules 的结果中。"""
        tech_keys = set()
        for o in self.tech_result.filter_outputs:
            details = o.details if isinstance(o.details, dict) else {}
            rk = details.get("rule_key")
            if rk:
                tech_keys.add(rk)

        macro_keys = {
            "macro_factor_analysis",
            "enterprise_potential_analysis",
            "company_event_hot_sector_link",
            "company_event_hot_news_link",
        }
        overlap = tech_keys & macro_keys
        self.assertEqual(
            overlap, set(),
            f"技术规则评估不应包含宏观规则: {overlap}",
        )
        self.assertEqual(len(tech_keys), 21, "技术规则应为 21 条（移除 rsi_oversold 后）")

    def test_24_macro_rules_dont_affect_technical_match_count(self):
        """total_match_count 仍只统计技术规则命中，不受宏观规则影响。"""
        # 合并后的 filter_outputs
        combined_outputs = list(self.tech_result.filter_outputs)
        combined_outputs.extend(self.macro_result.filter_outputs)

        # 只统计技术 PASS 的——与原有 total_match_count 一致
        tech_pass = sum(
            1 for o in self.tech_result.filter_outputs
            if o.result == FilterResult.PASS
        )
        self.assertEqual(
            tech_pass, self.tech_result.total_match_count,
            "技术 PASS 数量应等于 total_match_count",
        )

    def test_25_macro_result_passed_is_true(self):
        """宏观规则结果 passed=True（不参与阻断）。"""
        self.assertTrue(self.macro_result.passed)

    def test_26_print_macro_rule_outputs(self):
        """打印 4 条宏观规则的执行结果。"""
        print(f"\n{'='*80}")
        print(f"📊 HK.02685 ({STOCK_NAME}) Top20 后置宏观规则评估结果")
        print(f"{'='*80}")

        for o in self.macro_result.filter_outputs:
            details = o.details if isinstance(o.details, dict) else {}
            rk = details.get("rule_key", "?")
            icon = {"pass": "✅", "fail": "❌", "skip": "⊝", "error": "⚠️"}.get(
                o.result.value, "?"
            )
            print(f"  {icon} {rk} ({o.filter_name}): {o.result.value}")
            print(f"     reason: {o.reason[:120]}")
            # 打印关键 detail 字段
            for key in ("factor_count", "coverage_pct", "hot_sector_mark",
                        "matched_hot_sectors", "final_score", "decision"):
                val = details.get(key)
                if val is not None:
                    print(f"     {key}: {val}")
        print(f"{'='*80}\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
