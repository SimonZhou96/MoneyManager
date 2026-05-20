#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Macro strategizers backed by shared signal analysis results."""

from __future__ import annotations

from typing import Optional

from filters import FilterContext, StockInfo
from strategizers import Strategizer, StrategizerOutput
from signal_analysis.models import SignalAnalysisResult


_SIGNAL_ANALYSIS_LOADER_KEY = "signal_analysis_loader"


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
