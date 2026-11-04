#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Macro strategizers backed by shared signal analysis results."""

from __future__ import annotations

import math
from typing import Optional

if __package__:
    from .filters import FilterContext, StockInfo
    from .market_intel.evidence import EvidencePackBuilder
    from .market_intel.macro_scoring import (
        DEFAULT_MACRO_WEIGHT,
        DEFAULT_TECHNICAL_WEIGHT,
        MacroEvidencePreprocessor,
    )
    from .strategizers import Strategizer, StrategizerOutput
    from .signal_analysis.models import SignalAnalysisResult
else:
    from filters import FilterContext, StockInfo
    from market_intel.evidence import EvidencePackBuilder
    from market_intel.macro_scoring import (
        DEFAULT_MACRO_WEIGHT,
        DEFAULT_TECHNICAL_WEIGHT,
        MacroEvidencePreprocessor,
    )
    from strategizers import Strategizer, StrategizerOutput
    from signal_analysis.models import SignalAnalysisResult


_SIGNAL_ANALYSIS_LOADER_KEY = "signal_analysis_loader"
_MARKET_INTEL_SERVICE_KEY = "market_intel_service"
_MACRO_SCORE_SCORER_KEY = "macro_score_scorer"
_DEFAULT_MACRO_SCORE_THRESHOLD = 60.0
_REFRESH_POLICIES = {"cache_or_refresh", "force_refresh"}
_MISSING_MARKET_BUNDLE = object()


def _analysis_cache_key(code: str) -> str:
    return f"signal_analysis:{code}"


def _load_signal_analysis(stock: StockInfo, context: FilterContext) -> Optional[SignalAnalysisResult]:
    cached = context.get_cache(_analysis_cache_key(stock.code))
    if cached is not None:
        return cached
    loader = context.get_cache(_SIGNAL_ANALYSIS_LOADER_KEY)
    if callable(loader):
        analysis = loader(stock)
        if analysis is not None:
            context.set_cache(_analysis_cache_key(stock.code), analysis)
            return analysis
    return None


def _validate_threshold(value) -> float:
    if isinstance(value, bool):
        raise ValueError("MarketIntelMacroScoreStrategizer threshold must be a finite number, not bool")
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        raise ValueError("MarketIntelMacroScoreStrategizer threshold must be a finite number")
    if not math.isfinite(threshold):
        raise ValueError("MarketIntelMacroScoreStrategizer threshold must be finite")
    return threshold


def _validate_weight(name: str, value) -> float:
    if isinstance(value, bool):
        raise ValueError(f"MarketIntelMacroScoreStrategizer {name} must be a finite number, not bool")
    try:
        weight = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"MarketIntelMacroScoreStrategizer {name} must be a finite number")
    if not math.isfinite(weight):
        raise ValueError(f"MarketIntelMacroScoreStrategizer {name} must be finite")
    return weight


def _validate_refresh_policy(value) -> str:
    refresh_policy = str(value)
    if refresh_policy not in _REFRESH_POLICIES:
        allowed = ", ".join(sorted(_REFRESH_POLICIES))
        raise ValueError(f"MarketIntelMacroScoreStrategizer refresh_policy must be one of: {allowed}")
    return refresh_policy


def _market_bundle_cache_key(market: str, refresh_policy: str) -> str:
    return f"market_intel_market_bundle:{market}:{refresh_policy}"


def _get_cached_market_bundle(service, context: FilterContext, *, market: str, force_refresh: bool, refresh_policy: str):
    cache_key = _market_bundle_cache_key(market, refresh_policy)
    cached = context.get_cache(cache_key)
    if cached is _MISSING_MARKET_BUNDLE:
        return None
    if cached is not None:
        return cached

    getter = getattr(service, "get_market_digest", None)
    if not callable(getter):
        context.set_cache(cache_key, _MISSING_MARKET_BUNDLE)
        return None

    market_bundle = getter(market, force_refresh=force_refresh)
    context.set_cache(cache_key, market_bundle if market_bundle is not None else _MISSING_MARKET_BUNDLE)
    return market_bundle


def _temporal_findings_details(package) -> list:
    return [
        item.to_dict() if hasattr(item, "to_dict") else dict(item)
        for item in (getattr(package, "temporal_findings", []) or [])
    ]


class CompanyEventHotSectorStrategizer(Strategizer):
    def __init__(self, name: str = "CompanyEventHotSectorStrategizer", enabled: bool = True):
        super().__init__(name=name, enabled=enabled)

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        analysis = _load_signal_analysis(stock, context)
        if analysis is None or analysis.analysis_status != "success":
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                result="skip",
                reason="缺少宏观分析结果，无法判断公司时事与热点板块的关联",
                details={"code": stock.code},
            )
        if not analysis.company_events:
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                result="skip",
                reason="缺少公司时事，无法判断与热点板块是否形成共振",
                details={"code": stock.code},
            )
        if not analysis.hot_sectors and not analysis.matched_hot_sectors:
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                result="skip",
                reason="缺少热点板块数据，无法判断公司时事是否命中热点方向",
                details={"code": stock.code},
            )

        matched = list(analysis.matched_hot_sectors or [])
        is_pass = bool(matched) or analysis.hot_sector_mark in {"重点", "相关"}
        reason = (
            f"公司时事与热点板块形成共振：{'、'.join(matched) if matched else analysis.hot_sector_mark}"
            if is_pass
            else (analysis.hot_sector_reason or "未看到公司时事与热点板块形成明确共振")
        )
        return StrategizerOutput(
            name=self.name,
            satisfied=is_pass,
            result="pass" if is_pass else "fail",
            reason=reason,
            details={
                "company_events": list(analysis.company_events or []),
                "hot_sectors": list(analysis.hot_sectors or []),
                "matched_hot_sectors": matched,
                "hot_sector_mark": analysis.hot_sector_mark,
                "hot_sector_reason": analysis.hot_sector_reason,
            },
        )


class MarketIntelMacroScoreStrategizer(Strategizer):
    def __init__(
        self,
        threshold=60,
        refresh_policy: str = "cache_or_refresh",
        technical_weight=DEFAULT_TECHNICAL_WEIGHT,
        macro_weight=DEFAULT_MACRO_WEIGHT,
        name: str = "MarketIntelMacroScoreStrategizer",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)
        self.threshold = _validate_threshold(threshold)
        self.refresh_policy = _validate_refresh_policy(refresh_policy)
        self.technical_weight = _validate_weight("technical_weight", technical_weight)
        self.macro_weight = _validate_weight("macro_weight", macro_weight)

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        service = context.get_cache(_MARKET_INTEL_SERVICE_KEY)
        if service is None:
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                result="skip",
                reason="缺少 market_intel_service，无法执行宏观评分",
                details={"code": stock.code},
            )

        scorer = context.get_cache(_MACRO_SCORE_SCORER_KEY)
        if scorer is None:
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                result="error",
                reason="缺少 macro_score_scorer，无法执行宏观评分",
                details={"code": stock.code, "error": True},
            )

        market = stock.market or context.market
        force_refresh = self.refresh_policy == "force_refresh"
        try:
            market_bundle = _get_cached_market_bundle(
                service,
                context,
                market=market,
                force_refresh=force_refresh,
                refresh_policy=self.refresh_policy,
            )
            pack = EvidencePackBuilder(service).build(
                market=market,
                code=stock.code,
                market_bundle=market_bundle,
                force_refresh=force_refresh,
            )
            package = MacroEvidencePreprocessor().build(pack)
            source_status = dict(getattr(package, "source_status", {}) or {})
            data_gaps = list(getattr(package, "data_gaps", []) or [])
            temporal_findings = _temporal_findings_details(package)
            evidence_digest = package.evidence_digest() if hasattr(package, "evidence_digest") else ""

            if not package.has_scoreable_evidence:
                return StrategizerOutput(
                    name=self.name,
                    satisfied=False,
                    result="skip",
                    reason="缺少可评分的宏观证据，跳过宏观评分",
                    details={
                        "code": stock.code,
                        "source_status": source_status,
                        "data_gaps": data_gaps,
                        "temporal_findings": temporal_findings,
                        "evidence_digest": evidence_digest,
                        "technical_weight": self.technical_weight,
                        "macro_weight": self.macro_weight,
                    },
                )

            score = scorer.score(package, threshold=self.threshold)
            details = score.to_details()
            details.update({
                "code": stock.code,
                "model": str(getattr(scorer, "model_name", "") or ""),
                "evidence_digest": evidence_digest,
                "source_status": source_status,
                "data_gaps": data_gaps,
                "temporal_findings": temporal_findings,
                "technical_weight": self.technical_weight,
                "macro_weight": self.macro_weight,
            })
            is_pass = bool(score.passed)
            return StrategizerOutput(
                name=self.name,
                satisfied=is_pass,
                result="pass" if is_pass else "fail",
                reason=score.summary or ("宏观评分通过" if is_pass else "宏观评分未达标"),
                details=details,
            )
        except Exception as exc:
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                result="error",
                reason=f"宏观评分执行失败: {exc}",
                details={
                    "code": stock.code,
                    "error": True,
                    "exception_type": exc.__class__.__name__,
                    "phase": "macro_score",
                },
            )


class CompanyEventHotNewsStrategizer(Strategizer):
    _DIRECTIONAL_IMPACTS = {"利好", "偏利好", "利空", "偏利空"}

    def __init__(self, name: str = "CompanyEventHotNewsStrategizer", enabled: bool = True):
        super().__init__(name=name, enabled=enabled)

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        analysis = _load_signal_analysis(stock, context)
        if analysis is None or analysis.analysis_status != "success":
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                result="skip",
                reason="缺少宏观分析结果，无法判断公司时事与热点新闻的关联",
                details={"code": stock.code},
            )
        if not analysis.company_events:
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                result="skip",
                reason="缺少公司时事，无法判断是否被热点新闻验证",
                details={"code": stock.code},
            )
        if not analysis.company_hot_news and not analysis.market_hot_news:
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                result="skip",
                reason="缺少热点新闻数据，无法判断是否存在新闻验证",
                details={"code": stock.code},
            )

        company_news = list(analysis.company_hot_news or [])
        market_news = list(analysis.market_hot_news or [])
        directional = analysis.news_impact in self._DIRECTIONAL_IMPACTS
        is_pass = bool(company_news) and directional
        reason = (
            f"公司时事已被热点新闻验证，新闻影响为{analysis.news_impact}"
            if is_pass
            else (
                analysis.news_impact
                and f"新闻存在，但影响判断为{analysis.news_impact}，未形成明确验证"
                or "未看到足够明确的热点新闻验证"
            )
        )
        return StrategizerOutput(
            name=self.name,
            satisfied=is_pass,
            result="pass" if is_pass else "fail",
            reason=reason,
            details={
                "company_events": list(analysis.company_events or []),
                "company_hot_news": company_news,
                "market_hot_news": market_news,
                "news_impact": analysis.news_impact,
                "news_sources": list(analysis.news_sources or analysis.source_urls or []),
            },
        )
