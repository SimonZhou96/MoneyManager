#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企业潜力分析策略器"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    from ..filters import FilterContext, StockInfo
    from ..strategizers import Strategizer, StrategizerOutput
    from .builders import build_enterprise_evidence
    from .models import EnterprisePotentialEvidencePackage, MacroSnapshot
    from .providers import build_default_macro_provider
    from .scoring import DEFAULT_WEIGHTS, EnterprisePotentialScorer, EnterprisePotentialResult
except ImportError:
    import sys
    from pathlib import Path
    _parent = str(Path(__file__).resolve().parent.parent)
    if _parent not in sys.path:
        sys.path.insert(0, _parent)
    from filters import FilterContext, StockInfo
    from strategizers import Strategizer, StrategizerOutput
    from potential_analysis.builders import build_enterprise_evidence
    from potential_analysis.models import EnterprisePotentialEvidencePackage, MacroSnapshot
    from potential_analysis.providers import build_default_macro_provider
    from potential_analysis.scoring import DEFAULT_WEIGHTS, EnterprisePotentialScorer, EnterprisePotentialResult

logger = logging.getLogger(__name__)

_MACRO_SNAPSHOT_KEY = "macro_factor_snapshot"
_EVIDENCE_PACKAGE_KEY = "enterprise_evidence_package"


# ═══════════════════════════════════════════════════════════════════
# MacroFactorAnalysisStrategizer (之前已实现)
# ═══════════════════════════════════════════════════════════════════


class MacroFactorAnalysisStrategizer(Strategizer):
    """宏观因子采集与分析策略器"""

    def __init__(self, min_factors: int = 5,
                 name: str = "MacroFactorAnalysisStrategizer", enabled: bool = True):
        super().__init__(name=name, enabled=enabled)
        self.min_factors = max(1, int(min_factors))

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        market = stock.market or context.market
        snapshot = self._get_or_build_snapshot(market, context)
        return self._build_output(stock, market, snapshot)

    def _get_or_build_snapshot(self, market: str, context: FilterContext) -> MacroSnapshot:
        cache_key = f"{_MACRO_SNAPSHOT_KEY}:{market}"
        cached = context.get_cache(cache_key)
        if isinstance(cached, MacroSnapshot):
            return cached

        custom = context.get_cache("macro_factor_provider")
        if custom is not None and hasattr(custom, "fetch"):
            try:
                snapshot = custom.fetch(market)
            except Exception as e:
                logger.error(f"自定义 macro provider 失败: {e}")
                snapshot = MacroSnapshot(market=market, as_of=datetime.now(timezone.utc).isoformat(),
                                         provider_status={"custom": f"error: {e}"},
                                         data_gaps=[f"自定义 provider 失败: {e}"])
        else:
            try:
                snapshot = build_default_macro_provider(market)
            except Exception as e:
                logger.error(f"默认 macro provider 失败: {e}")
                snapshot = MacroSnapshot(market=market, as_of=datetime.now(timezone.utc).isoformat(),
                                         provider_status={"default": f"error: {e}"},
                                         data_gaps=[f"默认 provider 失败: {e}"])

        context.set_cache(cache_key, snapshot)
        return snapshot

    def _build_output(self, stock, market, snapshot) -> StrategizerOutput:
        fc = snapshot.factor_count()
        cov = snapshot.coverage_pct()
        total = len(snapshot.factors)

        if fc >= self.min_factors:
            satisfied, result, reason = True, "pass", \
                f"宏观因子采集成功: {fc}/{total} 有效 ({cov}%)"
        elif fc > 0:
            satisfied, result, reason = False, "skip", \
                f"宏观因子不足: 仅 {fc}/{total} 有效 ({cov}%), 需要至少 {self.min_factors}"
        else:
            satisfied, result, reason = False, "error", \
                f"宏观因子采集失败: 无有效因子, gaps={snapshot.data_gaps}"

        details = snapshot.to_dict()
        details.update({
            "code": stock.code, "stock_name": stock.name, "market": market,
            "factor_count": fc, "total_factors": total,
            "coverage_pct": cov, "min_factors_required": self.min_factors,
        })
        return StrategizerOutput(name=self.name, satisfied=satisfied,
                                 result=result, reason=reason, details=details)


# ═══════════════════════════════════════════════════════════════════
# EnterprisePotentialAnalysisStrategizer — 五模块综合评分
# ═══════════════════════════════════════════════════════════════════


class EnterprisePotentialAnalysisStrategizer(Strategizer):
    """
    企业潜力分析综合策略器。

    五模块评分模型：
      TotalScore = 0.30M + 0.25I + 0.25C + 0.10V + 0.10T

    其中 M=宏观, I=行业, C=企业质量, V=估值, T=交易行为

    流程：
    1. 构建五模块快照 (Macro/Industry/Company/Valuation/Trading)
    2. 封装为 EnterprisePotentialEvidencePackage
    3. 逐模块打分 + 加权求和
    4. 返回 pass/fail + 因果推理

    结果语义：
      - result="pass" : total_score >= threshold
      - result="fail" : total_score < threshold
      - result="skip" : 数据严重不足
      - result="error": 执行异常
    """

    def __init__(
        self,
        threshold: float = 70.0,
        weights: Optional[Dict[str, float]] = None,
        name: str = "EnterprisePotentialAnalysisStrategizer",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)
        self.threshold = float(threshold)
        self.weights = weights or DEFAULT_WEIGHTS
        self.scorer = EnterprisePotentialScorer(weights=self.weights, threshold=self.threshold)

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        market = stock.market or context.market
        code = stock.code
        name = stock.name

        try:
            # 1. 优先从缓存读取预拉取的证据包
            evidence = context.get_cache(f"enterprise:evidence:{code}")

            if evidence is None:
                # 缓存未命中 → 尝试从预拉取的模块快照组装
                service = context.get_cache("enterprise_service")
                if service is not None and hasattr(service, "load_evidence"):
                    evidence = service.load_evidence(market, code, name, context)
                else:
                    # 完全 fallback：逐股构建
                    macro = self._get_macro_snapshot(market, context)
                    evidence = build_enterprise_evidence(
                        market=market, code=code, name=name, macro_snapshot=macro)

            # 2. 注入 signal_analysis LLM 增强（热点/事件/新闻验证）
            self._inject_signal_analysis(evidence, context)

            # 3. 评分
            result = self.scorer.score(evidence)

            # 3. 构建输出
            return self._build_output(stock, evidence, result)

        except Exception as e:
            logger.exception(f"企业潜力分析失败: {e}")
            return StrategizerOutput(
                name=self.name, satisfied=False, result="error",
                reason=f"企业潜力分析执行失败: {e}",
                details={"code": code, "error": True, "exception": str(e)},
            )

    def _get_macro_snapshot(self, market: str, context: FilterContext) -> MacroSnapshot:
        """从上下文获取或新建宏观快照"""
        cache_key = f"{_MACRO_SNAPSHOT_KEY}:{market}"
        cached = context.get_cache(cache_key)
        if isinstance(cached, MacroSnapshot):
            return cached

        # 尝试通过 MacroFactorAnalysisStrategizer 的 provider 逻辑
        custom = context.get_cache("macro_factor_provider")
        if custom is not None and hasattr(custom, "fetch"):
            try:
                return custom.fetch(market)
            except Exception:
                pass

        return build_default_macro_provider(market)

    def _inject_signal_analysis(self, evidence: Any, context: FilterContext) -> None:
        """从 FilterContext 读取 signal_analysis LLM 结果，注入行业/企业快照"""
        code = evidence.code

        # 尝试从缓存获取 signal_analysis 结果
        analysis = context.get_cache(f"signal_analysis:{code}")
        if analysis is None:
            # 尝试 loader 闭包
            loader = context.get_cache("signal_analysis_loader")
            if callable(loader):
                try:
                    from filters import StockInfo
                    analysis = loader(StockInfo(
                        market=evidence.market, code=code, name=evidence.name))
                except Exception:
                    pass

        if analysis is None:
            return

        # ── 注入行业热点标签 ──
        if evidence.industry is not None:
            hot_sectors = list(getattr(analysis, "hot_sectors", []) or [])
            matched = list(getattr(analysis, "matched_hot_sectors", []) or [])
            all_hot = list(dict.fromkeys(hot_sectors + matched))  # 去重保持顺序
            if all_hot:
                evidence.industry.hot_sector_tags = all_hot

            # 行业景气阶段（来自 LLM 判定）
            mark = getattr(analysis, "hot_sector_mark", "")
            if mark in ("重点",):
                evidence.industry.policy_support = "strong"

        # ── 注入公司事件影响方向 ──
        if evidence.company is not None:
            impact = getattr(analysis, "news_impact", "")
            if impact:
                evidence.company.event_impact = str(impact)

            # 新闻验证
            company_news = getattr(analysis, "company_hot_news", []) or []
            if company_news:
                evidence.company.news_validation = True

    def _build_output(
        self, stock: StockInfo, evidence: EnterprisePotentialEvidencePackage,
        result: EnterprisePotentialResult,
    ) -> StrategizerOutput:
        details = result.to_details()
        details.update({
            "code": stock.code,
            "stock_name": stock.name,
            "market": evidence.market,
            "modules_available": evidence.modules_available,
            "modules_missing": evidence.modules_missing,
            "weights": dict(self.weights),
            "evidence_digest": evidence.evidence_digest(),
        })

        satisfied = result.passed
        r = "pass" if satisfied else "fail"
        reason = result.summary

        return StrategizerOutput(
            name=self.name, satisfied=satisfied,
            result=r, reason=reason, details=details,
        )


# ═══════════════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════════════


def analyze_enterprise_potential(market: str, code: str, name: str = "") -> Dict[str, Any]:
    """独立运行企业潜力分析，返回结构化 dict"""
    stock = StockInfo(market=market, code=code, name=name)
    context = FilterContext(check_date=datetime.now().date(), market=market)
    strategizer = EnterprisePotentialAnalysisStrategizer()
    output = strategizer.apply(stock, context)
    return {
        "satisfied": output.satisfied, "result": output.result,
        "reason": output.reason, "details": output.details,
    }
