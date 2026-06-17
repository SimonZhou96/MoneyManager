#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端测试：HK.02685 在 unified_bullish_top20 链中 DB → CSV → 报告的分数一致性。

验证点：
1. run_screening_task 产生的 DB record 的 filter_details 完整性
2. aggregate_rule_scores 公式正确（技术分/Macro分/综合分）
3. 统一评分权重与代码中 UNIFIED_SCORE_WEIGHTS 一致
4. DB / CSV / report 中公式口径无缺失
"""

import csv
import io
import os
import sys
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import MarketDatabase, MySqlConfig
from filters import FilterContext, FilterResult, StockInfo
from market_intel.macro_scoring import (
    DEFAULT_MACRO_WEIGHT,
    DEFAULT_TECHNICAL_WEIGHT,
    aggregate_rule_scores,
)
from rule_engine import (
    RuleEngine,
    RuleRepository,
    RuleRegistry,
)
from signal_analysis.models import (
    UNIFIED_SCORE_WEIGHTS,
    UnifiedScoreBreakdown,
    compute_unified_score,
)

# ═══════════════════════════════════════════════════════════════════
# 配置
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


class E2EUnifiedScoringConsistencyTest(unittest.TestCase):
    """端到端验证 DB / CSV / 报告 的评分一致性和公式口径正确性。"""

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

        # 1. 技术规则评估（全部股票）
        cls.tech_result = cls.engine.evaluate_bullish_technical_rules(
            cls.stock, cls.context
        )

        # 2. 模拟 Top20 后置宏观评估
        cls.macro_result = cls.engine.evaluate_macro_rules_for_top20(
            cls.stock, cls.context
        )

        # 3. 合并 filter_details（与 run_screening_task 中逻辑一致）
        cls._build_merged_filter_details()

        # 4. DB 层评分（aggregate_rule_scores）
        cls.db_scores = aggregate_rule_scores(
            cls.merged_filter_details,
            technical_weight=DEFAULT_TECHNICAL_WEIGHT,
            macro_weight=DEFAULT_MACRO_WEIGHT,
        )

        # 5. CSV 列映射
        cls.csv_row = cls._build_csv_row()

    @classmethod
    def _build_merged_filter_details(cls):
        """模拟 run_screening_task 中合并技术+宏观 filter_details 的逻辑。"""
        details = []
        for o in cls.tech_result.filter_outputs:
            d = o.details if isinstance(o.details, dict) else {}
            rk = d.get("rule_key", o.filter_name)
            meta = cls.engine.metadata_by_key.get(rk)
            details.append({
                "rule_key": meta.rule_key if meta else rk,
                "rule_type": meta.rule_type if meta else "",
                "strategy_category": meta.strategy_category if meta else "",
                "filter_name": o.filter_name,
                "result": o.result.value,
                "reason": o.reason or "",
                "details": _json_safe(d if d else {}),
            })
        for o in cls.macro_result.filter_outputs:
            d = o.details if isinstance(o.details, dict) else {}
            rk = d.get("rule_key", o.filter_name)
            meta = cls.engine.metadata_by_key.get(rk)
            details.append({
                "rule_key": meta.rule_key if meta else rk,
                "rule_type": meta.rule_type if meta else "",
                "strategy_category": meta.strategy_category if meta else "",
                "filter_name": o.filter_name,
                "result": o.result.value,
                "reason": o.reason or "",
                "details": _json_safe(d if d else {}),
            })

        cls.merged_filter_details = details

    @classmethod
    def _build_csv_row(cls):
        """模拟 CSV 中每只股票的行数据（从 DB record 映射，与 load_passed_screening_records 一致）。"""
        # 提取 enterprise 五模块分（与 scheduled_daily_job.load_passed_screening_records 一致）
        enterprise_scores = {}
        for item in cls.merged_filter_details:
            d = item.get("details") if isinstance(item.get("details"), dict) else {}
            if item.get("rule_key") == "enterprise_potential_analysis":
                for dk, record_key in (
                    ("macro_score", "宏观分"),
                    ("industry_score", "行业分"),
                    ("company_score", "企业质量分"),
                    ("valuation_score", "估值分"),
                    ("trading_score", "交易分"),
                    ("total_score", "五模块总分"),
                ):
                    val = d.get(dk)
                    if val is not None:
                        enterprise_scores[record_key] = val
                break

        raw = {**{str(k): str(v) for k, v in enterprise_scores.items()}, **enterprise_scores}

        return {
            "code": STOCK_CODE,
            "market": MARKET,
            "name": STOCK_NAME,
            "pe_ratio": "",
            "market_cap": "",
            "sector": "",
            "conditions_met": "|".join(cls.tech_result.bullish_condition_labels),
            "technical_score": cls.db_scores.get("technical_score"),
            "macro_score": cls.db_scores.get("macro_score"),
            "final_score": cls.db_scores.get("final_score"),
            "raw": raw,
            "main_force_risk_level": "",
            "main_force_risk_score": "",
            **enterprise_scores,
        }

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    # ── 1. 权重常量验证 ──────────────────────────────────────────

    def test_01_unified_score_weights_match_spec(self):
        """UNIFIED_SCORE_WEIGHTS 应与统一口径一致。"""
        self.assertEqual(UNIFIED_SCORE_WEIGHTS["technical"], 0.0,
                         "技术规则权重应为 0%")
        self.assertEqual(UNIFIED_SCORE_WEIGHTS["enterprise"], 0.40,
                         "五模块权重应为 40%")
        self.assertEqual(UNIFIED_SCORE_WEIGHTS["event_hot"], 0.30,
                         "事件热点权重应为 30%")
        self.assertEqual(UNIFIED_SCORE_WEIGHTS["fund_risk"], 0.20,
                         "资金风险权重应为 20%")
        self.assertEqual(UNIFIED_SCORE_WEIGHTS["llm"], 0.10,
                         "LLM复核权重应为 10%")
        self.assertAlmostEqual(sum(UNIFIED_SCORE_WEIGHTS.values()), 1.0,
                               msg="权重总和应为 100%")

    def test_02_db_scoring_defaults_match_unified_approach(self):
        """DB 层默认权重：技术 0%，宏观 100%。"""
        self.assertEqual(DEFAULT_TECHNICAL_WEIGHT, 0.0,
                         "DB 层技术权重应为 0")
        self.assertEqual(DEFAULT_MACRO_WEIGHT, 1.0,
                         "DB 层宏观权重应为 1.0")

    # ── 2. filter_details 完整性 ─────────────────────────────────

    def test_03_filter_details_has_26_rules(self):
        """合并后 filter_details 应有 22 技术 + 4 宏观 = 26 条。"""
        self.assertEqual(
            len(self.merged_filter_details), 26,
            f"期望 26 条规则（22 技术 + 4 宏观），实际 {len(self.merged_filter_details)}",
        )

    def test_04_technical_rules_have_strategy_category_technical(self):
        """前 22 条规则 strategy_category 应为 'technical'（含 energy_phase_bullish）。"""
        tech_items = [
            item for item in self.merged_filter_details
            if item.get("strategy_category") == "technical"
        ]
        self.assertEqual(len(tech_items), 22)

    def test_05_macro_rules_have_strategy_category_macro(self):
        """后 4 条规则 strategy_category 应为 'macro'。"""
        macro_items = [
            item for item in self.merged_filter_details
            if item.get("strategy_category") == "macro"
        ]
        self.assertEqual(len(macro_items), 4)

    # ── 3. DB 评分 (aggregate_rule_scores) ───────────────────────

    def test_06_db_technical_score_between_0_and_100(self):
        ts = self.db_scores.get("technical_score")
        self.assertIsNotNone(ts)
        self.assertGreaterEqual(ts, 0)
        self.assertLessEqual(ts, 100)

    def test_07_db_macro_score_present_when_enterprise_runs(self):
        """enterprise_potential_analysis 运行时，macro_score 应有值。"""
        # enterprise_potential_analysis detail 里含有 score 信息
        enterprise_items = [
            item for item in self.merged_filter_details
            if item.get("rule_key") == "enterprise_potential_analysis"
            and item.get("result") == "pass"
        ]
        if enterprise_items:
            ms = self.db_scores.get("macro_score")
            self.assertIsNotNone(ms, "有 enterprise 结果时 macro_score 不应为空")
            self.assertGreaterEqual(ms, 0)
            self.assertLessEqual(ms, 100)
        else:
            print("\n  ⚠️ enterprise_potential_analysis 未通过，跳过 macro_score 断言")

    def test_08_db_final_score_formula(self):
        """DB final_score = technical_score * 0.0 + macro_score * 1.0 = macro_score。"""
        ts = self.db_scores.get("technical_score") or 0.0
        ms = self.db_scores.get("macro_score")
        fs = self.db_scores.get("final_score")
        if ms is not None:
            expected = round(ts * 0.0 + ms * 1.0, 1)
            self.assertAlmostEqual(
                fs, expected, places=1,
                msg=f"final_score({fs}) 应 = macro_score({ms}) when technical_weight=0",
            )

    def test_09_db_score_weights_in_summary(self):
        """score_details 中应包含权重信息。"""
        self.assertEqual(self.db_scores.get("technical_weight"), 0.0)
        self.assertEqual(self.db_scores.get("macro_weight"), 1.0)

    # ── 4. 报告公式口径 ──────────────────────────────────────────

    def test_10_compute_unified_score_formula_string(self):
        """compute_unified_score 生成的 formula 字符串包含正确权重。"""
        from datetime import datetime, timezone
        from signal_analysis.models import ScreeningSignalRow, SignalAnalysisResult

        # SignalAnalysisResult（实际构造器，无 market/market_label 等字段）
        analysis = SignalAnalysisResult(
            code=STOCK_CODE, name=STOCK_NAME,
            analysis_status="success",
            signal_bias="bullish",
            reliability_score=65.0, confidence_score=60.0,
            summary="测试摘要",
            positive_factors=["放量突破"], risk_factors=[],
            macro_factors=["PMI回升"],
            company_events=["签订AI订单"], company_hot_news=[],
            market_hot_news=[], hot_sectors=["AI"], matched_hot_sectors=["AI"],
            hot_sector_mark="重点", hot_sector_reason="AI板块持续走强",
            hot_sector_relevance="0.9", news_impact="正面",
            news_sources=[], source_urls=[],
            evidence_links=[], factor_citations={},
            data_gaps=[], model="test",
        )

        # ScreeningSignalRow（frozen=True，使用真实字段）
        row = ScreeningSignalRow(
            index=0, code=STOCK_CODE, market=MARKET, market_label="港股",
            name=STOCK_NAME, pe_ratio="", market_cap="", sector="",
            conditions_met="|".join(self.tech_result.bullish_condition_labels),
            instrument_type="股票",
            main_force_risk_level="", main_force_risk_score="",
            raw=self.csv_row["raw"],
        )

        unified = compute_unified_score(analysis, row)

        # 验证公式字符串包含正确权重
        self.assertIn("0%", unified.formula, "公式应包含技术权重 0%")
        self.assertIn("40%", unified.formula, "公式应包含五模块权重 40%")
        self.assertIn("30%", unified.formula, "公式应包含事件热点权重 30%")
        self.assertIn("20%", unified.formula, "公式应包含资金风险权重 20%")
        self.assertIn("10%", unified.formula, "公式应包含 LLM 权重 10%")

        # 验证 CSV 列映射
        csv_cols = unified.to_csv_columns()
        self.assertIn("最终统一评分", csv_cols)
        self.assertIn("最终评分公式", csv_cols)
        self.assertIn("技术规则分", csv_cols)
        self.assertIn("宏观五模块分", csv_cols)
        self.assertIn("事件热点分", csv_cols)
        self.assertIn("资金风险分", csv_cols)
        self.assertIn("LLM复核分", csv_cols)
        self.assertIn("评分缺失项", csv_cols)

        print(f"\n  ✓ 公式: {unified.formula}")
        print(f"  ✓ CSV列: {list(csv_cols.keys())}")

    # ── 5. DB record 字段不缺失 ───────────────────────────────────

    def test_11_db_record_must_have_all_score_fields(self):
        """模拟的 DB record 应包含所有必需的评分字段。"""
        required_fields = [
            "technical_score", "macro_score", "final_score",
            "technical_weight", "macro_weight",
        ]
        for field in required_fields:
            self.assertIn(field, self.db_scores, f"DB record 缺少字段: {field}")

    # ── 6. 计算可复现性验证 ───────────────────────────────────────

    def test_12_db_technical_score_manual_recompute(self):
        """手动从 filter_details 重算技术分，验证与 aggregate_rule_scores 一致。"""
        tech_passes = []
        for item in self.merged_filter_details:
            if item.get("strategy_category") != "macro" and item.get("rule_type") == "strategy":
                tech_passes.append(100.0 if item.get("result") == "pass" else 0.0)

        manual_tech = sum(tech_passes) / len(tech_passes) if tech_passes else None
        db_tech = self.db_scores.get("technical_score")

        if manual_tech is not None and db_tech is not None:
            self.assertAlmostEqual(
                manual_tech, db_tech, places=1,
                msg=f"手动技术分({manual_tech}) ≠ DB技术分({db_tech})",
            )
            print(f"\n  ✓ 技术分验证: 手动={manual_tech:.1f}, DB={db_tech:.1f} "
                  f"(PASS {int(sum(1 for p in tech_passes if p == 100))}/{len(tech_passes)})")

    def test_13_macro_factor_analysis_in_filter_details(self):
        """macro_factor_analysis 的 detail 应包含 factor_count / coverage_pct。"""
        for item in self.merged_filter_details:
            if item.get("rule_key") == "macro_factor_analysis":
                details = item.get("details") or {}
                self.assertIn("factor_count", details,
                              "macro_factor_analysis 缺少 factor_count")
                self.assertIn("coverage_pct", details,
                              "macro_factor_analysis 缺少 coverage_pct")
                return
        self.fail("filter_details 中未找到 macro_factor_analysis")

    def test_14_enterprise_potential_detail_has_decision(self):
        """enterprise_potential_analysis 的 detail 应包含五模块分和决策。"""
        for item in self.merged_filter_details:
            if item.get("rule_key") == "enterprise_potential_analysis":
                details = item.get("details") or {}
                result = item.get("result")
                if result == "pass":
                    self.assertIn("total_score", details,
                                  "enterprise_potential_analysis 缺少 total_score")
                    self.assertIn("decision", details,
                                  "enterprise_potential_analysis 缺少 decision")
                return
        self.fail("filter_details 中未找到 enterprise_potential_analysis")

    def test_14a_enterprise_scores_in_csv_row(self):
        """CSV 行应包含五模块企业潜力评分列（宏观分/行业分/企业质量分/估值分/交易分/五模块总分）。"""
        expected_keys = ["宏观分", "行业分", "企业质量分", "估值分", "交易分", "五模块总分"]
        for key in expected_keys:
            val = self.csv_row.get(key)
            self.assertIsNotNone(
                val, f"CSV 行缺少列: {key}（enterprise_potential_analysis 未产出数据？）"
            )
            if val is not None:
                print(f"\n  ✓ {key}: {val}")

        # 同时验证 raw dict 也包含这些键（供 _enterprise_unified_score 使用）
        raw = self.csv_row.get("raw", {})
        for key in expected_keys:
            self.assertIn(key, raw, f"raw dict 缺少键: {key}")

    # ── 7. 打印综合结果 ───────────────────────────────────────────

    def test_15_print_full_e2e_summary(self):
        """打印端到端评分汇总。"""
        print(f"\n{'='*80}")
        print(f"📊 E2E 评分一致性测试 — {STOCK_CODE} ({STOCK_NAME})")
        print(f"{'='*80}")

        # 技术评分
        ts = self.db_scores.get("technical_score")
        ms = self.db_scores.get("macro_score")
        fs = self.db_scores.get("final_score")
        tw = self.db_scores.get("technical_weight")
        mw = self.db_scores.get("macro_weight")

        print(f"\n  ── DB 评分 (aggregate_rule_scores) ──")
        print(f"  技术分: {ts:.1f} (权重: {tw})")
        if ms is not None:
            print(f"  宏观分: {ms:.1f} (权重: {mw})")
            print(f"  综合分: {fs:.1f}")
            print(f"  公式: {ts:.1f}×{tw} + {ms:.1f}×{mw} = {fs:.1f}")
        else:
            print(f"  宏观分: N/A (权重: {mw})")
            print(f"  综合分: N/A")
            print("  ⚠️ 宏观分缺失")

        print(f"\n  ── 统一评分口径 ──")
        print(f"  技术×0% + 五模块×40% + 事件热点×30% + 资金风险×20% + LLM×10%")

        print(f"\n  ── filter_details 统计 ──")
        tech_count = len([i for i in self.merged_filter_details if i.get("strategy_category") == "technical"])
        macro_count = len([i for i in self.merged_filter_details if i.get("strategy_category") == "macro"])
        pass_count = len([i for i in self.merged_filter_details if i.get("result") == "pass"])
        fail_count = len([i for i in self.merged_filter_details if i.get("result") == "fail"])
        skip_count = len([i for i in self.merged_filter_details if i.get("result") == "skip"])
        print(f"  总数: {len(self.merged_filter_details)} (技术 {tech_count} + 宏观 {macro_count})")
        print(f"  PASS: {pass_count}, FAIL: {fail_count}, SKIP: {skip_count}")

        print(f"\n  ── 技术命中 ──")
        for label in self.tech_result.bullish_condition_labels:
            print(f"    ✅ {label}")

        print(f"\n  ── 宏观规则结果 ──")
        for o in self.macro_result.filter_outputs:
            d = o.details if isinstance(o.details, dict) else {}
            rk = d.get("rule_key", "?")
            print(f"    {o.result.value:5s} {rk}: {o.reason[:100]}")

        print(f"{'='*80}\n")


def _json_safe(value):
    """递归转换为 JSON 可序列化的值（与 screen_service 中一致）。"""
    import math
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
