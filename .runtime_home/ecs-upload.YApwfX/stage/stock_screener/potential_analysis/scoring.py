#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企业潜力分析 — 评分与解析

V1 实现规则型评分器（不依赖 LLM），每个模块用阈值打分。
V2 可替换为 LLM 评分器。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .holding_period import (
    decision_from_total_score,
    normalize_holding_period,
    suggest_holding_period,
)
from .models import (
    CompanySnapshot,
    EnterprisePotentialEvidencePackage,
    IndustrySnapshot,
    MacroSnapshot,
    TradingSnapshot,
    ValuationSnapshot,
)


# ═══════════════════════════════════════════════════════════════════
# 默认权重 (来自设计文档 9.1)
# ═══════════════════════════════════════════════════════════════════

DEFAULT_WEIGHTS = {
    "macro": 0.30,
    "industry": 0.25,
    "company": 0.25,
    "valuation": 0.10,
    "trading": 0.10,
}


# ═══════════════════════════════════════════════════════════════════
# 模块评分器
# ═══════════════════════════════════════════════════════════════════


class ModuleScorer:
    """单个模块的评分器基类"""

    def score(self, snapshot: Any) -> float:
        """返回 0~100 的模块分数"""
        raise NotImplementedError


class MacroScorer(ModuleScorer):
    """宏观评分 — 基于宏观因子快照"""

    def score(self, macro: MacroSnapshot) -> float:
        score = 50.0  # 中性基准
        count = 0

        # 利率：低利率 + 降息 → 利多
        if macro.lpr_1y is not None:
            count += 1
            if macro.lpr_1y <= 3.5:
                score += 10
            elif macro.lpr_1y <= 4.5:
                score += 5
        if macro.ten_year_yield is not None:
            count += 1
            if macro.ten_year_yield <= 3.0:
                score += 10

        # 通胀：温和通胀利好
        if macro.cpi_yoy is not None:
            count += 1
            if 1.0 <= macro.cpi_yoy <= 3.0:
                score += 10
            elif 0 <= macro.cpi_yoy < 1.0:
                score += 5

        # 经济增长：PMI > 50 扩张
        if macro.pmi_manufacturing is not None:
            count += 1
            if macro.pmi_manufacturing >= 52:
                score += 15
            elif macro.pmi_manufacturing >= 50:
                score += 10
            elif macro.pmi_manufacturing >= 48:
                score += 5

        # M2：适度宽松
        if macro.m2_yoy is not None:
            count += 1
            if 8 <= macro.m2_yoy <= 12:
                score += 10
            elif macro.m2_yoy > 12:
                score += 5

        # VIX：低恐慌偏好
        if macro.vix is not None:
            count += 1
            if macro.vix <= 20:
                score += 10
            elif macro.vix <= 25:
                score += 5
            elif macro.vix > 30:
                score -= 10

        # DXY：弱美元利好新兴市场
        if macro.dxy is not None:
            count += 1
            if macro.dxy <= 100:
                score += 5
            elif macro.dxy >= 105:
                score -= 5

        if count == 0:
            return 50.0  # 无数据返回中性
        return round(max(0, min(100, score)), 1)


class CompanyScorer(ModuleScorer):
    """企业质量评分 — 结构化财务 + LLM 事件/新闻增强"""

    _POSITIVE_IMPACTS = {"利好", "偏利好"}
    _NEGATIVE_IMPACTS = {"利空", "偏利空"}

    def score(self, company: CompanySnapshot) -> float:
        score = 50.0
        count = 0

        # ROIC/ROE
        roic = company.roic or company.roe
        if roic is not None:
            count += 1
            if roic >= 20:
                score += 20
            elif roic >= 15:
                score += 15
            elif roic >= 10:
                score += 10
            elif roic >= 5:
                score += 5
            else:
                score -= 10

        # 毛利率
        if company.gross_margin is not None:
            count += 1
            if company.gross_margin >= 40:
                score += 15
            elif company.gross_margin >= 30:
                score += 10
            elif company.gross_margin >= 20:
                score += 5

        # 净利率
        if company.net_margin is not None:
            count += 1
            if company.net_margin >= 15:
                score += 10
            elif company.net_margin >= 8:
                score += 5
            else:
                score -= 5

        # 收入增速
        if company.revenue_growth is not None:
            count += 1
            if company.revenue_growth >= 20:
                score += 15
            elif company.revenue_growth >= 10:
                score += 10
            elif company.revenue_growth >= 0:
                score += 5
            elif company.revenue_growth < -10:
                score -= 10

        # 负债
        if company.debt_to_equity is not None:
            count += 1
            if company.debt_to_equity <= 50:
                score += 10
            elif company.debt_to_equity <= 100:
                score += 5

        # ── LLM 增强：公司事件方向 ──
        if company.event_impact in self._POSITIVE_IMPACTS:
            score += 10
        elif company.event_impact in self._NEGATIVE_IMPACTS:
            score -= 10

        # ── LLM 增强：热点新闻验证 ──
        if company.news_validation:
            score += 5

        if count == 0 and not company.event_impact and not company.news_validation:
            return 50.0
        return round(max(0, min(100, score)), 1)


class ValuationScorer(ModuleScorer):
    """估值评分 — 越低越有吸引力"""

    def score(self, val: ValuationSnapshot) -> float:
        score = 50.0
        count = 0

        if val.pe_trailing is not None:
            count += 1
            if val.pe_trailing <= 12:
                score += 20
            elif val.pe_trailing <= 18:
                score += 15
            elif val.pe_trailing <= 25:
                score += 10
            elif val.pe_trailing <= 35:
                score += 5
            else:
                score -= 10

        if val.pb is not None:
            count += 1
            if val.pb <= 1.5:
                score += 10
            elif val.pb <= 3:
                score += 5

        if val.peg is not None:
            count += 1
            if val.peg <= 1.0:
                score += 15
            elif val.peg <= 2.0:
                score += 10
            elif val.peg <= 3.0:
                score += 5

        if val.ev_ebitda is not None:
            count += 1
            if val.ev_ebitda <= 10:
                score += 10
            elif val.ev_ebitda <= 15:
                score += 5

        if count == 0:
            return 50.0
        return round(max(0, min(100, score)), 1)


class IndustryScorer(ModuleScorer):
    """行业景气评分 — 结构化标签 + LLM 热点增强"""

    def score(self, ind: IndustrySnapshot) -> float:
        score = 50.0
        if ind.sector:
            score += 5
        # 热点板块标签（来自 signal_analysis LLM 的 hot_sectors）
        if ind.hot_sector_tags:
            score += 10
            # 多个热点标签 → 更强的行业共振
            if len(ind.hot_sector_tags) >= 2:
                score += 5
        if ind.industry_growth_stage in ("expansion", "early_recovery"):
            score += 10
        elif ind.industry_growth_stage == "contraction":
            score -= 10
        if ind.policy_support == "strong":
            score += 10
        return round(max(0, min(100, score)), 1)


class TradingScorer(ModuleScorer):
    """市场行为评分"""

    def score(self, tr: TradingSnapshot) -> float:
        score = 50.0
        count = 0

        if tr.pct_change is not None:
            count += 1
            if tr.pct_change > 0:
                score += min(tr.pct_change * 2, 15)

        if tr.price_vs_50ma is not None:
            count += 1
            if -5 <= tr.price_vs_50ma <= 5:
                score += 5  # 不极端偏离
            elif tr.price_vs_50ma > 5:
                score += 3

        if tr.volatility_30d is not None:
            count += 1
            if tr.volatility_30d <= 30:
                score += 10
            elif tr.volatility_30d <= 50:
                score += 5
            elif tr.volatility_30d > 70:
                score -= 10

        if tr.beta is not None:
            count += 1
            if 0.5 <= tr.beta <= 1.5:
                score += 5

        if tr.trend_strength is not None:
            count += 1
            score += (tr.trend_strength - 50) * 0.3  # 趋势强度加成

        if count == 0:
            return 50.0
        return round(max(0, min(100, score)), 1)


# ═══════════════════════════════════════════════════════════════════
# 综合评分结果
# ═══════════════════════════════════════════════════════════════════


@dataclass
class EnterprisePotentialResult:
    """企业潜力分析综合评分结果"""

    ticker: str
    total_score: float = 0.0

    macro_score: Optional[float] = None
    industry_score: Optional[float] = None
    company_score: Optional[float] = None
    valuation_score: Optional[float] = None
    trading_score: Optional[float] = None

    passed: bool = False
    threshold: float = 70.0
    confidence_score: float = 0.0
    decision: str = ""   # "BUY" / "WATCH" / "SKIP"
    holding_period: str = ""

    main_drivers: List[str] = field(default_factory=list)
    main_risks: List[str] = field(default_factory=list)
    causal_chain: List[str] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)
    data_gaps: List[str] = field(default_factory=list)

    summary: str = ""

    module_scores: Dict[str, Optional[float]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "total_score": self.total_score,
            "macro_score": self.macro_score,
            "industry_score": self.industry_score,
            "company_score": self.company_score,
            "valuation_score": self.valuation_score,
            "trading_score": self.trading_score,
            "passed": self.passed,
            "threshold": self.threshold,
            "confidence_score": self.confidence_score,
            "decision": self.decision,
            "holding_period": self.holding_period,
            "main_drivers": list(self.main_drivers),
            "main_risks": list(self.main_risks),
            "causal_chain": list(self.causal_chain),
            "data_gaps": list(self.data_gaps),
            "summary": self.summary,
        }

    def to_details(self) -> Dict[str, Any]:
        """对齐 StrategizerOutput.details 格式"""
        return self.to_dict()


# ═══════════════════════════════════════════════════════════════════
# 综合评分器
# ═══════════════════════════════════════════════════════════════════


class EnterprisePotentialScorer:
    """
    企业潜力综合分析评分器。

    两种模式：
    1. 规则型（默认）— 各模块独立打分 + 加权求和
    2. LLM 评分 — 将 EvidencePackage digest 发送给 LLM，获取因果推理

    规则型始终作为 LLM 不可用时的 fallback。
    """

    def __init__(self, weights: Optional[Dict[str, float]] = None,
                 threshold: float = 70.0, llm_client=None):
        self.weights = weights or DEFAULT_WEIGHTS
        self.threshold = threshold
        self._llm_client = llm_client
        self._scorers = {
            "macro": MacroScorer(), "company": CompanyScorer(),
            "valuation": ValuationScorer(), "industry": IndustryScorer(),
            "trading": TradingScorer(),
        }

    def score(self, evidence: EnterprisePotentialEvidencePackage) -> EnterprisePotentialResult:
        """单只股票评分（批量模式也走这里，当 N=1）"""
        return self._rule_score(evidence)

    def score_batch(self, packages: List[EnterprisePotentialEvidencePackage]
                    ) -> List[EnterprisePotentialResult]:
        """批量评分 — 优先 LLM，回退规则型"""
        if self._llm_client and len(packages) > 0:
            try:
                return self._llm_score_batch(packages)
            except Exception as e:
                logger.warning(f"LLM 批量评分失败, fallback 规则型: {e}")
        return [self._rule_score(p) for p in packages]

    def _llm_score_batch(self, packages: List[EnterprisePotentialEvidencePackage]
                         ) -> List[EnterprisePotentialResult]:
        """LLM 批量评分"""
        from .prompting import (
            EnterprisePotentialParser,
            EnterprisePotentialPromptBuilder,
            SYSTEM_PROMPT,
        )

        builder = EnterprisePotentialPromptBuilder()
        parser = EnterprisePotentialParser()

        if len(packages) == 1:
            prompt = builder.build_single_prompt(packages[0])
        else:
            prompt = builder.build_batch_prompt(packages)

        raw = self._llm_client.complete_json(prompt, system=SYSTEM_PROMPT)
        items = parser.parse(raw)
        results = []

        for i, item in enumerate(items):
            item = parser.validate(item)
            pkg = packages[i] if i < len(packages) else packages[0]
            r = EnterprisePotentialResult(
                ticker=f"{pkg.market}.{pkg.code}",
                total_score=item.get("total_score", 50),
                macro_score=item.get("macro_score"),
                industry_score=item.get("industry_score"),
                company_score=item.get("company_score"),
                valuation_score=item.get("valuation_score"),
                trading_score=item.get("trading_score"),
                decision=item.get("decision", "SKIP"),
                holding_period=item.get("holding_period", ""),
                confidence_score=item.get("confidence_score", 50),
                main_drivers=item.get("main_drivers", []),
                main_risks=item.get("main_risks", []),
                causal_chain=item.get("causal_chain", []),
                data_gaps=item.get("data_gaps", []),
                threshold=self.threshold,
            )
            module_scores = {
                "macro": r.macro_score,
                "industry": r.industry_score,
                "company": r.company_score,
                "valuation": r.valuation_score,
                "trading": r.trading_score,
            }
            self._finalize_result(r, module_scores)
            results.append(r)

        return results

    def _finalize_result(
        self,
        result: EnterprisePotentialResult,
        module_scores: Dict[str, Optional[float]],
    ) -> None:
        """统一决策、建议持有周期与摘要文案（规则与 LLM 两路共用）。"""
        result.passed = result.total_score >= result.threshold
        result.decision = decision_from_total_score(result.total_score, result.threshold)
        result.module_scores = module_scores

        existing = normalize_holding_period(result.holding_period)
        result.holding_period = existing or suggest_holding_period(
            result.decision,
            module_scores,
            self.weights,
            result.total_score,
            result.threshold,
        )

        mod_parts = [f"{m}={s:.0f}" for m, s in module_scores.items() if s is not None]
        summary = f"总分 {result.total_score:.1f} (阈值 {result.threshold}) → {result.decision}"
        if result.holding_period:
            summary += f" | 建议持有周期: {result.holding_period}"
        if mod_parts:
            summary += " | " + ", ".join(mod_parts)
        result.summary = summary

    def _rule_score(self, evidence: EnterprisePotentialEvidencePackage
                    ) -> EnterprisePotentialResult:
        """规则型评分"""
        result = EnterprisePotentialResult(
            ticker=f"{evidence.market}.{evidence.code}",
            threshold=self.threshold,
        )

        scores = {}
        modules_scored = 0
        data_gaps = list(evidence.data_gaps or [])

        # 逐模块评分
        if evidence.macro:
            try:
                scores["macro"] = self._scorers["macro"].score(evidence.macro)
            except Exception:
                data_gaps.append("macro 评分异常")
        if evidence.company:
            try:
                scores["company"] = self._scorers["company"].score(evidence.company)
            except Exception:
                data_gaps.append("company 评分异常")
        if evidence.valuation:
            try:
                scores["valuation"] = self._scorers["valuation"].score(evidence.valuation)
            except Exception:
                data_gaps.append("valuation 评分异常")
        if evidence.industry:
            try:
                scores["industry"] = self._scorers["industry"].score(evidence.industry)
            except Exception:
                data_gaps.append("industry 评分异常")
        if evidence.trading:
            try:
                scores["trading"] = self._scorers["trading"].score(evidence.trading)
            except Exception:
                data_gaps.append("trading 评分异常")

        # 缺失模块用 50 分（中性）补齐
        for mod in ["macro", "industry", "company", "valuation", "trading"]:
            if mod not in scores:
                if mod in evidence.modules_missing:
                    data_gaps.append(f"模块缺失: {mod}")
                scores[mod] = 50.0

        result.macro_score = scores.get("macro")
        result.company_score = scores.get("company")
        result.valuation_score = scores.get("valuation")
        result.industry_score = scores.get("industry")
        result.trading_score = scores.get("trading")

        # 加权总分
        total = 0.0
        weight_sum = 0.0
        for mod, weight in self.weights.items():
            if mod in scores:
                total += scores[mod] * weight
                weight_sum += weight
        result.total_score = round(total / weight_sum, 1) if weight_sum > 0 else 50.0

        # 置信度 = 可用模块数 / 5
        result.confidence_score = round(len(evidence.modules_available) / 5 * 100, 1)

        result.data_gaps = data_gaps

        # 驱动因素与风险
        drivers, risks = self._analyze_factors(scores, evidence)
        result.main_drivers = drivers
        result.main_risks = risks

        # 决策 + 建议持有周期 + 摘要（与 LLM 路径统一）
        self._finalize_result(result, scores)
        return result

    def _analyze_factors(self, scores: Dict, evidence) -> tuple:
        """从各模块分数中提炼驱动因素和风险"""
        drivers = []
        risks = []

        if scores.get("macro", 50) >= 70:
            drivers.append("宏观环境支持")
        elif scores.get("macro", 50) < 40:
            risks.append("宏观环境压力")

        if scores.get("company", 50) >= 70:
            drivers.append("企业质量优秀")
        elif scores.get("company", 50) < 40:
            risks.append("企业质量偏弱")

        if scores.get("valuation", 50) >= 70:
            drivers.append("估值有吸引力")
        elif scores.get("valuation", 50) < 35:
            risks.append("估值偏高")

        if scores.get("industry", 50) >= 65:
            drivers.append("行业景气")
        elif scores.get("industry", 50) < 40:
            risks.append("行业不景气")

        if scores.get("trading", 50) >= 65:
            drivers.append("技术面强势")
        elif scores.get("trading", 50) < 35:
            risks.append("技术面弱势")

        if evidence.data_gaps:
            risks.append(f"数据缺口: {len(evidence.data_gaps)}项")

        return drivers, risks
