#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Domain models for post-screening signal analysis."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple


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


def _list_of_dicts(value: Any) -> List[dict]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _dict_of_link_lists(value: Any) -> Dict[str, List[dict]]:
    if not isinstance(value, dict):
        return {}
    result: Dict[str, List[dict]] = {}
    for key, links in value.items():
        result[str(key)] = _list_of_dicts(links)
    return result


def _json_string(value: Any) -> str:
    if value in (None, [], {}):
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


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

# 统一评分权重：技术规则决定 Top20 入围，不参与最终评分（0%）；
# 五模块/事件热点/资金风险/LLM 复核构成最终评分口径。
UNIFIED_SCORE_WEIGHTS = {
    "technical": 0.00,
    "enterprise": 0.40,
    "event_hot": 0.30,
    "fund_risk": 0.20,
    "llm": 0.10,
}


def _clamp_score(value: float) -> float:
    return max(0.0, min(100.0, round(float(value), 1)))


def _split_conditions(text: str) -> List[str]:
    return [part.strip() for part in (text or "").split("|") if part.strip()]


def _float_from_row(raw: Dict[str, str], *keys: str) -> Optional[float]:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            parsed = _optional_float(value)
            if parsed is not None:
                return parsed
    return None


def _technical_unified_score(row: Optional["ScreeningSignalRow"]) -> Tuple[float, List[str]]:
    if row is None:
        return 50.0, ["技术规则分缺失，按50中性补齐"]

    conditions = _split_conditions(row.conditions_met)
    if not conditions:
        return 50.0, ["技术规则未命中，按50中性补齐"]

    bullish = sum(1 for item in conditions if item.startswith("看涨:"))
    rebound = sum(1 for item in conditions if item.startswith("准备反弹:"))
    zuoyi = sum(1 for item in conditions if item.startswith("左一看涨:"))
    generic = len(conditions) - bullish - rebound - zuoyi
    score = 50.0 + bullish * 5.0 + rebound * 6.0 + zuoyi * 8.0 + generic * 4.0
    if any("放量" in item for item in conditions):
        score += 4.0
    if any("RSI超卖" in item or "RSI超卖回升" in item for item in conditions):
        score += 3.0
    if any("看跌" in item or "超买" in item for item in conditions):
        score -= 8.0
    return _clamp_score(score), []


def _enterprise_unified_score(raw: Dict[str, str]) -> Tuple[float, List[str], Dict[str, Optional[float]]]:
    module_scores = {
        "macro": _float_from_row(raw, "宏观分", "macro_score", "enterprise_macro_score"),
        "industry": _float_from_row(raw, "行业分", "industry_score", "enterprise_industry_score"),
        "company": _float_from_row(raw, "企业质量分", "company_score", "enterprise_company_score"),
        "valuation": _float_from_row(raw, "估值分", "valuation_score", "enterprise_valuation_score"),
        "trading": _float_from_row(raw, "交易分", "trading_score", "enterprise_trading_score"),
    }
    total = _float_from_row(raw, "宏观五模块分", "五模块总分", "enterprise_total_score", "enterprise_potential_score")
    missing = [name for name, value in module_scores.items() if value is None]
    if total is None:
        filled = {name: (value if value is not None else 50.0) for name, value in module_scores.items()}
        weights = {"macro": 0.20, "industry": 0.20, "company": 0.25, "valuation": 0.15, "trading": 0.20}
        total = sum(filled[name] * weights[name] for name in weights)
    labels = {
        "macro": "宏观",
        "industry": "行业",
        "company": "企业质量",
        "valuation": "估值",
        "trading": "交易",
    }
    missing_notes = [f"五模块{labels.get(name, name)}缺失按50补齐" for name in missing]
    return _clamp_score(total), missing_notes, module_scores


def _event_hot_unified_score(result: "SignalAnalysisResult") -> Tuple[float, List[str]]:
    score = 50.0
    missing = []
    if result.company_events:
        score += min(10.0, len(result.company_events) * 4.0)
    else:
        missing.append("公司事件缺失按50基线处理")
    if result.company_hot_news:
        score += min(8.0, len(result.company_hot_news) * 4.0)
    if result.market_hot_news:
        score += min(6.0, len(result.market_hot_news) * 2.0)

    mark = result.hot_sector_mark or ""
    if mark == "重点":
        score += 15.0
    elif mark == "相关":
        score += 10.0
    elif mark == "观察":
        score += 5.0
    elif not mark:
        missing.append("热点匹配缺失按50基线处理")

    impact = (result.news_impact or "").lower()
    if any(word in impact for word in ("负面", "利空", "negative", "bearish")):
        score -= 12.0
    elif any(word in impact for word in ("正面", "利好", "positive", "bullish")):
        score += 6.0
    return _clamp_score(score), missing


def _fund_risk_unified_score(row: Optional["ScreeningSignalRow"]) -> Tuple[float, List[str]]:
    if row is None:
        return 50.0, ["资金风险数据缺失按50中性补齐"]
    score_value = _optional_float(row.main_force_risk_score)
    if score_value is not None:
        return _clamp_score(100.0 - score_value), []
    level = (row.main_force_risk_level or "").strip()
    if level == "高":
        return 35.0, []
    if level == "中":
        return 55.0, []
    if level in {"低", "无", "无明显风险"}:
        return 80.0, []
    return 50.0, ["主力资金风险缺失按50中性补齐"]


def _llm_unified_score(result: "SignalAnalysisResult") -> Tuple[float, List[str]]:
    missing = []
    reliability = result.reliability_score
    confidence = result.confidence_score
    if reliability is None:
        reliability = 50.0
        missing.append("LLM复核分缺失按50中性补齐")
    if confidence is None:
        score = float(reliability)
    else:
        score = float(reliability) * 0.8 + float(confidence) * 0.2
    score += min(6.0, len(result.positive_factors) * 2.0)
    score -= min(8.0, len(result.risk_factors) * 2.0)
    if result.signal_bias in {"bearish", "avoid"}:
        score -= 10.0
    elif result.signal_bias == "bullish":
        score += 4.0
    return _clamp_score(score), missing


@dataclass(frozen=True)
class UnifiedScoreBreakdown:
    final_score: float
    formula: str
    technical_score: float
    enterprise_score: float
    event_hot_score: float
    fund_risk_score: float
    llm_score: float
    missing_items: List[str] = field(default_factory=list)
    enterprise_module_scores: Dict[str, Optional[float]] = field(default_factory=dict)

    def to_csv_columns(self) -> Dict[str, str]:
        module = self.enterprise_module_scores
        return {
            "最终统一评分": f"{self.final_score:.1f}",
            "最终评分公式": self.formula,
            "技术规则分": f"{self.technical_score:.1f}",
            "宏观五模块分": f"{self.enterprise_score:.1f}",
            "事件热点分": f"{self.event_hot_score:.1f}",
            "资金风险分": f"{self.fund_risk_score:.1f}",
            "LLM复核分": f"{self.llm_score:.1f}",
            "评分缺失项": "；".join(self.missing_items),
            "宏观分": "" if module.get("macro") is None else f"{module['macro']:.1f}",
            "行业分": "" if module.get("industry") is None else f"{module['industry']:.1f}",
            "企业质量分": "" if module.get("company") is None else f"{module['company']:.1f}",
            "估值分": "" if module.get("valuation") is None else f"{module['valuation']:.1f}",
            "交易分": "" if module.get("trading") is None else f"{module['trading']:.1f}",
        }


def compute_unified_score(
    result: "SignalAnalysisResult",
    row: Optional["ScreeningSignalRow"] = None,
) -> UnifiedScoreBreakdown:
    raw = row.raw if row is not None else {}
    technical_score, technical_missing = _technical_unified_score(row)
    enterprise_score, enterprise_missing, module_scores = _enterprise_unified_score(raw)
    event_hot_score, event_hot_missing = _event_hot_unified_score(result)
    fund_risk_score, fund_missing = _fund_risk_unified_score(row)
    llm_score, llm_missing = _llm_unified_score(result)

    final = (
        technical_score * UNIFIED_SCORE_WEIGHTS["technical"]
        + enterprise_score * UNIFIED_SCORE_WEIGHTS["enterprise"]
        + event_hot_score * UNIFIED_SCORE_WEIGHTS["event_hot"]
        + fund_risk_score * UNIFIED_SCORE_WEIGHTS["fund_risk"]
        + llm_score * UNIFIED_SCORE_WEIGHTS["llm"]
    )
    final = _clamp_score(final)
    formula = (
        f"技术 {technical_score:.1f}*0% + "
        f"五模块 {enterprise_score:.1f}*40% + "
        f"事件热点 {event_hot_score:.1f}*30% + "
        f"资金风险 {fund_risk_score:.1f}*20% + "
        f"LLM复核 {llm_score:.1f}*10% = {final:.1f}"
    )
    return UnifiedScoreBreakdown(
        final_score=final,
        formula=formula,
        technical_score=technical_score,
        enterprise_score=enterprise_score,
        event_hot_score=event_hot_score,
        fund_risk_score=fund_risk_score,
        llm_score=llm_score,
        missing_items=[
            *technical_missing,
            *enterprise_missing,
            *event_hot_missing,
            *fund_missing,
            *llm_missing,
        ],
        enterprise_module_scores=module_scores,
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
    data_gaps: List[str] = field(default_factory=list)
    evidence_links: List[dict] = field(default_factory=list)
    factor_citations: Dict[str, List[dict]] = field(default_factory=dict)
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
            data_gaps=_list_of_strings(item.get("data_gaps") or item.get("数据缺失原因")),
            evidence_links=_list_of_dicts(item.get("evidence_links") or item.get("引用来源")),
            factor_citations=_dict_of_link_lists(item.get("factor_citations") or item.get("因素引用")),
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

    def to_csv_columns(self, row: Optional[ScreeningSignalRow] = None) -> Dict[str, str]:
        unified = compute_unified_score(self, row)
        columns = {
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
            "数据缺失原因": "；".join(self.data_gaps),
            "引用来源": _json_string(self.evidence_links),
            "因素引用": _json_string(self.factor_citations),
        }
        columns.update(unified.to_csv_columns())
        return columns

    def to_db_row(
        self,
        task_id: str,
        market: str,
        check_date: date,
        csv_path: str,
        timeframe: str = "1d",
        analysis_profile: str = "default",
    ) -> Dict[str, Any]:
        return {
            "task_id": task_id,
            "market": market,
            "code": self.code,
            "name": self.name,
            "check_date": check_date,
            "csv_path": csv_path,
            "timeframe": timeframe,
            "analysis_profile": analysis_profile,
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
            "data_gaps": self.data_gaps,
            "evidence_links": self.evidence_links,
            "factor_citations": self.factor_citations,
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
    evidence_packs: Dict[str, dict] = field(default_factory=dict)
    analyzed_count: int = 0
    skipped_reason: str = ""
