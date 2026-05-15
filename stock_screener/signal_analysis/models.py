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


def _normalize_instrument_type(row: Dict[str, Any]) -> str:
    value = _string(row.get("标的类型") or row.get("instrument_type")).upper()
    if value in {"ETF", "基金", "FUND"}:
        return "ETF"
    if value in {"股票", "STOCK"}:
        return "股票"

    legacy_etf = _string(row.get("是否ETF") or row.get("is_etf")).lower()
    if legacy_etf in {"1", "true", "yes", "y", "是", "etf"}:
        return "ETF"

    sector = _string(row.get("所属板块") or row.get("sector") or row.get("industry")).upper()
    name = _string(row.get("名称") or row.get("name")).upper()
    if sector == "ETF" or " ETF" in f" {sector} ":
        return "ETF"
    if any(keyword in name for keyword in ("ETF", "ETN", "基金", "EXCHANGE TRADED FUND")):
        return "ETF"
    return "股票"


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
HOT_SECTOR_MARK_CRITERIA = (
    "重点=与热点板块直接匹配；相关=存在产业链/政策/概念关联；"
    "观察=暂无明确匹配但可跟踪轮动；无明确关联=当前信息看不出关联；未知=信息不足"
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
    instrument_type: str = "股票"
    main_force_risk_level: str = ""
    main_force_risk_score: str = ""
    main_force_risk_signals: str = ""
    main_force_risk_summary: str = ""
    main_force_market_data_observation: str = ""
    main_force_fund_flow_data: str = ""
    main_force_order_book_data: str = ""
    main_force_lhb_data: str = ""
    main_force_chip_data: str = ""
    main_force_missing_data: str = ""
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
            instrument_type=_normalize_instrument_type(row),
            conditions_met=_string(row.get("满足的条件") or row.get("conditions_met")),
            main_force_risk_level=_string(row.get("主力流出风险") or row.get("main_force_risk_level")),
            main_force_risk_score=_string(row.get("主力风险分") or row.get("main_force_risk_score")),
            main_force_risk_signals=_string(row.get("主力风险信号") or row.get("main_force_risk_signals")),
            main_force_risk_summary=_string(row.get("主力风险说明") or row.get("main_force_risk_summary")),
            main_force_market_data_observation=_string(
                row.get("资金与盘面观察")
                or row.get("资金与盘口观察")
                or row.get("main_force_market_data_observation")
            ),
            main_force_fund_flow_data=_string(row.get("资金流向数据") or row.get("main_force_fund_flow_data")),
            main_force_order_book_data=_string(row.get("盘口数据") or row.get("main_force_order_book_data")),
            main_force_lhb_data=_string(row.get("龙虎榜数据") or row.get("main_force_lhb_data")),
            main_force_chip_data=_string(
                row.get("成交量分布数据")
                or row.get("筹码分布数据")
                or row.get("main_force_chip_data")
            ),
            main_force_missing_data=_string(row.get("数据不足项") or row.get("main_force_missing_data")),
            raw={_string(k): _string(v) for k, v in row.items()},
        )

    @property
    def is_etf(self) -> bool:
        return self.instrument_type == "ETF"

    def to_prompt_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "market": self.market,
            "market_label": self.market_label,
            "sector": self.sector,
            "instrument_type": self.instrument_type,
            "pe_ratio": self.pe_ratio,
            "market_cap": self.market_cap,
            "conditions_met": self.conditions_met,
            "main_force_risk_level": self.main_force_risk_level,
            "main_force_risk_score": self.main_force_risk_score,
            "main_force_risk_signals": self.main_force_risk_signals,
            "main_force_risk_summary": self.main_force_risk_summary,
            "main_force_market_data_observation": self.main_force_market_data_observation,
            "main_force_data_gap": self.main_force_missing_data,
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
    hot_sectors: List[str] = field(default_factory=list)
    hot_sector_mark: str = ""
    matched_hot_sectors: List[str] = field(default_factory=list)
    hot_sector_relevance: str = ""
    hot_sector_reason: str = ""
    hot_sector_sources: List[str] = field(default_factory=list)
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
            hot_sectors=_list_of_strings(item.get("hot_sectors")),
            hot_sector_mark=_string(item.get("hot_sector_mark")),
            matched_hot_sectors=_list_of_strings(item.get("matched_hot_sectors")),
            hot_sector_relevance=_string(item.get("hot_sector_relevance")),
            hot_sector_reason=_string(item.get("hot_sector_reason")),
            hot_sector_sources=_list_of_strings(item.get("hot_sector_sources")),
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
            "AI识别热点板块": "；".join(self.hot_sectors),
            "热点板块标记": self.hot_sector_mark,
            "匹配热点板块": "；".join(self.matched_hot_sectors),
            "热点板块关联度": self.hot_sector_relevance,
            "热点板块匹配理由": self.hot_sector_reason,
            "热点板块来源": "；".join(self.hot_sector_sources),
            "热点板块标记口径": HOT_SECTOR_MARK_CRITERIA,
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
            "hot_sectors": self.hot_sectors,
            "hot_sector_mark": self.hot_sector_mark,
            "matched_hot_sectors": self.matched_hot_sectors,
            "hot_sector_relevance": self.hot_sector_relevance,
            "hot_sector_reason": self.hot_sector_reason,
            "hot_sector_sources": self.hot_sector_sources,
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
