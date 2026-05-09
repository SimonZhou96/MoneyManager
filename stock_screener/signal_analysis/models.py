#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Domain models for post-screening signal analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _list_of_strings(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_string(item) for item in value if _string(item)]
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return [_string(value)] if _string(value) else []


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


RELIABILITY_SCORE_CRITERIA = (
    "0-100；80-100信号较强，60-79可关注，40-59偏弱或信息混杂，"
    "20-39可靠性低，0-19明显风险或外部信息否定信号"
)
CONFIDENCE_SCORE_CRITERIA = (
    "0-100；衡量模型对本次判断的信心，受信息来源充分性、搜索结果一致性、"
    "公司事件明确性影响"
)
SIGNAL_BIAS_CRITERIA = (
    "bullish偏看涨；bearish偏看跌；neutral方向不明确；"
    "avoid风险明显建议回避；unknown信息不足"
)


@dataclass(frozen=True)
class ScreeningSignalRow:
    """One stock row loaded from a screening result CSV."""

    index: int
    code: str
    market: str
    market_label: str
    name: str
    pe_ratio: str
    market_cap: str
    sector: str
    conditions_met: str
    raw: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_csv_row(cls, row: Dict[str, str], index: int, default_market: str) -> "ScreeningSignalRow":
        code = _string(row.get("股票代码") or row.get("code"))
        market_label = _string(row.get("市场") or row.get("market") or default_market)
        return cls(
            index=index,
            code=code,
            market=default_market,
            market_label=market_label,
            name=_string(row.get("名称") or row.get("name") or code),
            pe_ratio=_string(row.get("pe") or row.get("pe_ratio")),
            market_cap=_string(row.get("市值") or row.get("market_cap")),
            sector=_string(row.get("所属板块") or row.get("sector") or row.get("industry")),
            conditions_met=_string(row.get("满足的条件") or row.get("conditions_met")),
            raw={_string(k): _string(v) for k, v in row.items()},
        )

    def to_prompt_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "market": self.market,
            "market_label": self.market_label,
            "sector": self.sector,
            "pe_ratio": self.pe_ratio,
            "market_cap": self.market_cap,
            "conditions_met": self.conditions_met,
        }


@dataclass(frozen=True)
class SearchDocument:
    """Normalized search result snippet."""

    title: str
    url: str
    content: str
    score: Optional[float] = None
    query: str = ""

    def to_prompt_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "content": self.content,
            "score": self.score,
        }


@dataclass
class SignalAnalysisResult:
    """LLM's best-effort judgement for one stock signal."""

    code: str
    name: str = ""
    analysis_status: str = "success"
    reliability_score: Optional[float] = None
    confidence_score: Optional[float] = None
    signal_bias: str = "unknown"
    summary: str = ""
    positive_factors: List[str] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)
    macro_factors: List[str] = field(default_factory=list)
    company_events: List[str] = field(default_factory=list)
    market_hot_news: List[str] = field(default_factory=list)
    company_hot_news: List[str] = field(default_factory=list)
    news_impact: str = ""
    news_sources: List[str] = field(default_factory=list)
    source_urls: List[str] = field(default_factory=list)
    model: str = ""
    raw_response: Any = None
    error_message: str = ""

    @classmethod
    def from_llm_item(cls, item: Dict[str, Any], model: str) -> "SignalAnalysisResult":
        return cls(
            code=_string(item.get("code")),
            name=_string(item.get("name")),
            analysis_status=_string(item.get("analysis_status") or "success"),
            reliability_score=_optional_float(item.get("reliability_score")),
            confidence_score=_optional_float(item.get("confidence_score")),
            signal_bias=_string(item.get("signal_bias") or "unknown"),
            summary=_string(item.get("summary")),
            positive_factors=_list_of_strings(item.get("positive_factors")),
            risk_factors=_list_of_strings(item.get("risk_factors")),
            macro_factors=_list_of_strings(item.get("macro_factors")),
            company_events=_list_of_strings(item.get("company_events")),
            market_hot_news=_list_of_strings(item.get("market_hot_news")),
            company_hot_news=_list_of_strings(item.get("company_hot_news")),
            news_impact=_string(item.get("news_impact")),
            news_sources=_list_of_strings(item.get("news_sources")),
            source_urls=_list_of_strings(item.get("source_urls")),
            model=model,
            raw_response=item,
            error_message=_string(item.get("error_message")),
        )

    @classmethod
    def error(cls, row: ScreeningSignalRow, message: str, model: str = "") -> "SignalAnalysisResult":
        return cls(
            code=row.code,
            name=row.name,
            analysis_status="error",
            signal_bias="unknown",
            model=model,
            error_message=message,
        )

    def to_csv_columns(self) -> Dict[str, str]:
        return {
            "AI分析状态": self.analysis_status,
            "信号可靠性评分": "" if self.reliability_score is None else f"{self.reliability_score:.2f}",
            "信号可靠性评分口径": RELIABILITY_SCORE_CRITERIA,
            "模型置信度": "" if self.confidence_score is None else f"{self.confidence_score:.2f}",
            "模型置信度口径": CONFIDENCE_SCORE_CRITERIA,
            "辅助方向判断": self.signal_bias,
            "辅助方向判断口径": SIGNAL_BIAS_CRITERIA,
            "关键利好因素": "；".join(self.positive_factors),
            "关键风险因素": "；".join(self.risk_factors),
            "宏观/政策因素": "；".join(self.macro_factors),
            "公司事件": "；".join(self.company_events),
            "市场热点新闻": "；".join(self.market_hot_news),
            "公司热点新闻": "；".join(self.company_hot_news),
            "新闻影响判断": self.news_impact,
            "新闻来源": "；".join(self.news_sources),
            "信息来源": "；".join(self.source_urls),
        }

    def to_db_row(
        self,
        task_id: str,
        market: str,
        check_date: date,
        csv_path: str,
    ) -> Dict[str, Any]:
        return {
            "task_id": task_id,
            "market": market,
            "code": self.code,
            "name": self.name,
            "check_date": check_date,
            "csv_path": csv_path,
            "analysis_status": self.analysis_status,
            "reliability_score": self.reliability_score,
            "confidence_score": self.confidence_score,
            "signal_bias": self.signal_bias,
            "summary": self.summary,
            "positive_factors": self.positive_factors,
            "risk_factors": self.risk_factors,
            "macro_factors": self.macro_factors,
            "company_events": self.company_events,
            "market_hot_news": self.market_hot_news,
            "company_hot_news": self.company_hot_news,
            "news_impact": self.news_impact,
            "news_sources": self.news_sources,
            "source_urls": self.source_urls,
            "model": self.model,
            "raw_response": self.raw_response,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class AnalysisSettings:
    """Runtime knobs for signal analysis."""

    batch_size: int = 20
    timeout_sec: int = 120
    search_max_results: int = 5


@dataclass
class AnalysisRunResult:
    """Result returned to the scheduler after best-effort analysis."""

    success: bool
    artifact_paths: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    results_by_code: Dict[str, SignalAnalysisResult] = field(default_factory=dict)
    analyzed_count: int = 0
    skipped_reason: str = ""
