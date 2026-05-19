from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Protocol

from signal_analysis.evidence import (
    build_data_gaps,
    build_evidence_links,
    build_factor_citations,
    clean_result_factors,
    dedupe,
    dedupe_documents,
    expand_company_documents,
)
from signal_analysis.models import AnalysisSettings, ScreeningSignalRow, SignalAnalysisResult
from signal_analysis.models import SearchDocument

from .models import MarketSnapshot, StrategyCandidate


MACRO_ANALYSIS_PROFILE = "default"


class OptionMacroAnalysisProvider(Protocol):
    def analyze(self, *, market: str, code: str, snapshot: MarketSnapshot, ttl_minutes: int) -> "OptionMacroAnalysis":
        ...


@dataclass(frozen=True)
class OptionMacroAnalysis:
    macro_score: Optional[float]
    macro_direction: str
    news_impact: str
    hot_sector_mark: str
    main_force_risk_level: str
    summary: str
    positive_factors: List[str]
    risk_factors: List[str]
    macro_factors: List[str]
    source_urls: List[str]
    warnings: List[str]
    cached: bool
    provider: str
    analyzed_at: str
    expires_at: str
    data_gaps: List[str] = field(default_factory=list)
    evidence_links: List[dict] = field(default_factory=list)
    factor_citations: Dict[str, List[dict]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "macro_score": self.macro_score,
            "macro_direction": self.macro_direction,
            "news_impact": self.news_impact,
            "hot_sector_mark": self.hot_sector_mark,
            "main_force_risk_level": self.main_force_risk_level,
            "summary": self.summary,
            "positive_factors": list(self.positive_factors),
            "risk_factors": list(self.risk_factors),
            "macro_factors": list(self.macro_factors),
            "source_urls": list(self.source_urls),
            "warnings": list(self.warnings),
            "cached": self.cached,
            "provider": self.provider,
            "analyzed_at": self.analyzed_at,
            "expires_at": self.expires_at,
            "data_gaps": list(self.data_gaps),
            "evidence_links": [dict(item) for item in self.evidence_links],
            "factor_citations": {
                str(key): [dict(item) for item in value]
                for key, value in self.factor_citations.items()
            },
            "宏观分析评分": self.macro_score,
            "宏观方向": self.macro_direction,
            "新闻影响": self.news_impact,
            "热点匹配": self.hot_sector_mark,
            "主力资金风险": self.main_force_risk_level,
            "宏观摘要": self.summary,
            "关键利好因素": list(self.positive_factors),
            "关键风险因素": list(self.risk_factors),
            "宏观/政策因素": list(self.macro_factors),
            "信息来源": list(self.source_urls),
            "数据缺失原因": list(self.data_gaps),
            "引用来源": [dict(item) for item in self.evidence_links],
            "因素引用": {
                str(key): [dict(item) for item in value]
                for key, value in self.factor_citations.items()
            },
        }

    def to_cache_row(self, market: str, code: str, analysis_profile: str = MACRO_ANALYSIS_PROFILE) -> dict:
        return {
            **self.to_dict(),
            "cache_key": cache_key_for_macro_analysis(market, code, analysis_profile),
            "market": market,
            "code": code,
            "analysis_profile": analysis_profile,
            "raw_payload": self.to_dict(),
        }

    def with_cached(self, cached: bool, warnings: Optional[List[str]] = None) -> "OptionMacroAnalysis":
        return replace(self, cached=cached, warnings=list(warnings if warnings is not None else self.warnings))

    @classmethod
    def from_cache_row(cls, row: dict) -> "OptionMacroAnalysis":
        payload = row.get("raw_payload") or row
        return cls(
            macro_score=_optional_float(payload.get("macro_score") or payload.get("宏观分析评分")),
            macro_direction=str(payload.get("macro_direction") or payload.get("宏观方向") or "信息不足"),
            news_impact=str(payload.get("news_impact") or payload.get("新闻影响") or "信息不足"),
            hot_sector_mark=str(payload.get("hot_sector_mark") or payload.get("热点匹配") or "未知"),
            main_force_risk_level=str(payload.get("main_force_risk_level") or payload.get("主力资金风险") or "数据不足"),
            summary=str(payload.get("summary") or payload.get("宏观摘要") or ""),
            positive_factors=_list(payload.get("positive_factors") or payload.get("关键利好因素")),
            risk_factors=_list(payload.get("risk_factors") or payload.get("关键风险因素")),
            macro_factors=_list(payload.get("macro_factors") or payload.get("宏观/政策因素")),
            source_urls=_list(payload.get("source_urls") or payload.get("信息来源")),
            warnings=_list(payload.get("warnings")),
            cached=bool(payload.get("cached")),
            provider=str(payload.get("provider") or ""),
            analyzed_at=str(payload.get("analyzed_at") or row.get("created_at") or ""),
            expires_at=str(payload.get("expires_at") or row.get("expires_at") or ""),
            data_gaps=_list(payload.get("data_gaps") or payload.get("数据缺失原因")),
            evidence_links=_list_of_dicts(payload.get("evidence_links") or payload.get("引用来源")),
            factor_citations=_dict_of_link_lists(payload.get("factor_citations") or payload.get("因素引用")),
        )


class NullOptionMacroAnalysisProvider:
    def analyze(self, *, market: str, code: str, snapshot: MarketSnapshot, ttl_minutes: int) -> OptionMacroAnalysis:
        now = _now()
        return OptionMacroAnalysis(
            macro_score=None,
            macro_direction="信息不足",
            news_impact="信息不足",
            hot_sector_mark="未知",
            main_force_risk_level="数据不足",
            summary="未配置 LLM provider，跳过宏观分析",
            positive_factors=[],
            risk_factors=[],
            macro_factors=[],
            source_urls=[],
            warnings=["未配置 LLM provider，跳过宏观分析"],
            cached=False,
            provider="null",
            analyzed_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=ttl_minutes)).isoformat(),
        )


class SignalOptionMacroAnalysisProvider:
    def __init__(self, settings: Optional[AnalysisSettings] = None):
        self.settings = settings or AnalysisSettings()

    def analyze(self, *, market: str, code: str, snapshot: MarketSnapshot, ttl_minutes: int) -> OptionMacroAnalysis:
        from signal_analysis.chain import (
            ApplyManualHotNewsStep,
            BuildSearchQueriesStep,
            LLMBatchAnalysisStep,
            NormalizeAnalysisStep,
            ResolveHotSectorsStep,
            SearchContextStep,
            SignalAnalysisContext,
        )
        from signal_analysis.factories import LLMProviderFactory, SearchProviderFactory
        from signal_analysis.hot_news import ManualHotNewsConfig
        from signal_analysis.hot_sectors import ManualHotSectorConfig
        from signal_analysis.service import _prepare_analysis_providers, settings_from_env

        settings = settings_from_env()
        search_provider = SearchProviderFactory.from_env(settings)
        llm_provider = LLMProviderFactory.from_env(settings)
        search_provider, llm_provider, warnings = _prepare_analysis_providers(search_provider, llm_provider)
        if not getattr(llm_provider, "is_available", False):
            return NullOptionMacroAnalysisProvider().analyze(
                market=market,
                code=code,
                snapshot=snapshot,
                ttl_minutes=ttl_minutes,
            ).with_cached(False, warnings + ["未配置 LLM provider，跳过宏观分析"])

        row = screening_row_from_snapshot(market, code, snapshot)
        context = SignalAnalysisContext(
            task_id=f"option-macro-{market}-{code}",
            market=market,
            csv_path="",
            check_date=date.today(),
            settings=settings,
            search_provider=search_provider,
            llm_provider=llm_provider,
            manual_hot_news=ManualHotNewsConfig.from_env(market),
            manual_hot_sectors=ManualHotSectorConfig.from_env(market),
            rows=[row],
            warnings=list(warnings),
        )
        for step in (BuildSearchQueriesStep(), SearchContextStep(), ApplyManualHotNewsStep()):
            if context.aborted:
                break
            step.run(context)
        if not context.aborted:
            try:
                expanded_docs = expand_option_company_documents(
                    search_provider=context.search_provider,
                    market=market,
                    row=row,
                    max_results=context.settings.search_max_results,
                )
                context.company_documents[row.code] = dedupe_documents([
                    *(context.company_documents.get(row.code) or []),
                    *expanded_docs,
                ])
            except Exception as exc:
                context.warnings.append(f"期权宏观来源拓展搜索失败: {type(exc).__name__}: {exc}")
        for step in (ResolveHotSectorsStep(), LLMBatchAnalysisStep(), NormalizeAnalysisStep()):
            if context.aborted:
                break
            step.run(context)
        result = context.results_by_code.get(code)
        if result is None:
            result = SignalAnalysisResult.error(row, context.skipped_reason or "模型未返回该股票结果", llm_provider.model_name)
        return macro_analysis_from_signal_result(
            result=result,
            warnings=context.warnings,
            provider=llm_provider.model_name,
            ttl_minutes=ttl_minutes,
            row=row,
            source_documents=[
                *context.market_documents,
                *context.sector_documents,
                *(context.company_documents.get(code) or []),
            ],
        )


def build_default_option_macro_analysis_provider() -> OptionMacroAnalysisProvider:
    return SignalOptionMacroAnalysisProvider()


def apply_macro_analysis_to_candidates(
    candidates: Iterable[StrategyCandidate],
    macro_analysis: OptionMacroAnalysis,
) -> List[StrategyCandidate]:
    rows = []
    for candidate in candidates:
        composite_score = None
        if macro_analysis.macro_score is not None:
            composite_score = round(float(candidate.score) * 0.7 + float(macro_analysis.macro_score) * 0.3, 2)
        rows.append(
            replace(
                candidate,
                option_score=candidate.score,
                macro_score=macro_analysis.macro_score,
                composite_score=composite_score,
                macro_direction=macro_analysis.macro_direction,
                news_impact=macro_analysis.news_impact,
                hot_sector_mark=macro_analysis.hot_sector_mark,
                main_force_risk_level=macro_analysis.main_force_risk_level,
                macro_summary=macro_analysis.summary,
                macro_positive_factors=list(macro_analysis.positive_factors),
                macro_risk_factors=list(macro_analysis.risk_factors),
                macro_factors=list(macro_analysis.macro_factors),
                macro_source_urls=list(macro_analysis.source_urls),
                macro_data_gaps=list(macro_analysis.data_gaps),
                macro_evidence_links=[dict(item) for item in macro_analysis.evidence_links],
                macro_factor_citations={
                    key: [dict(item) for item in value]
                    for key, value in macro_analysis.factor_citations.items()
                },
            )
        )
    return rows


def macro_analysis_from_signal_result(
    result: SignalAnalysisResult,
    warnings: List[str],
    provider: str,
    ttl_minutes: int,
    row: Optional[ScreeningSignalRow] = None,
    source_documents: Optional[List[SearchDocument]] = None,
) -> OptionMacroAnalysis:
    now = _now()
    clean_result_factors(result)
    evidence_links = build_evidence_links(source_documents or [], result.source_urls or result.news_sources)
    source_urls = dedupe([item["url"] for item in evidence_links] or list(result.source_urls or result.news_sources))
    factor_citations = build_factor_citations(
        [*result.positive_factors, *result.risk_factors, *result.macro_factors],
        source_documents or [],
        evidence_links,
    )
    data_gaps = build_data_gaps(
        row=row,
        result=result,
        source_documents=source_documents or [],
        factor_citations=factor_citations,
        main_force_context_label="期权实验室",
    )
    return OptionMacroAnalysis(
        macro_score=result.reliability_score,
        macro_direction=_direction_label(result.signal_bias),
        news_impact=result.news_impact or "信息不足",
        hot_sector_mark=result.hot_sector_mark or "未知",
        main_force_risk_level=(row.main_force_risk_level if row and row.main_force_risk_level else "数据不足"),
        summary=result.summary,
        positive_factors=list(result.positive_factors),
        risk_factors=list(result.risk_factors),
        macro_factors=list(result.macro_factors),
        source_urls=source_urls,
        warnings=list(warnings),
        cached=False,
        provider=provider,
        analyzed_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=ttl_minutes)).isoformat(),
        data_gaps=data_gaps,
        evidence_links=evidence_links,
        factor_citations=factor_citations,
    )


def screening_row_from_snapshot(market: str, code: str, snapshot: MarketSnapshot) -> ScreeningSignalRow:
    return ScreeningSignalRow(
        index=0,
        code=code,
        market=market,
        market_label=market,
        name=snapshot.underlying.name or code,
        pe_ratio="",
        market_cap="",
        sector=str(snapshot.underlying.signal_summary.get("所属板块") or snapshot.underlying.signal_summary.get("sector") or ""),
        conditions_met=str(snapshot.underlying.signal_summary or {}),
        instrument_type=_instrument_type_for_market(market, code),
        main_force_risk_level=str(snapshot.underlying.signal_summary.get("主力流出风险") or "数据不足"),
        main_force_risk_score=str(snapshot.underlying.signal_summary.get("主力风险分") or ""),
        main_force_risk_signals=str(snapshot.underlying.signal_summary.get("主力风险信号") or ""),
        main_force_risk_summary=str(snapshot.underlying.signal_summary.get("主力风险说明") or ""),
        main_force_market_data_observation=str(
            snapshot.underlying.signal_summary.get("资金与盘面观察")
            or snapshot.underlying.signal_summary.get("资金与盘口观察")
            or ""
        ),
        main_force_fund_flow_data=str(snapshot.underlying.signal_summary.get("资金流向数据") or ""),
        main_force_order_book_data=str(snapshot.underlying.signal_summary.get("盘口数据") or ""),
        main_force_lhb_data=str(snapshot.underlying.signal_summary.get("龙虎榜数据") or ""),
        main_force_chip_data=str(
            snapshot.underlying.signal_summary.get("成交量分布数据")
            or snapshot.underlying.signal_summary.get("筹码分布数据")
            or ""
        ),
        main_force_missing_data=str(snapshot.underlying.signal_summary.get("数据不足项") or ""),
        raw=snapshot.underlying.to_dict(),
    )


def expand_option_company_documents(
    search_provider,
    market: str,
    row: ScreeningSignalRow,
    max_results: int,
) -> List[SearchDocument]:
    return expand_company_documents(search_provider, market, row, max_results)


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


def _dedupe(items: List[str]) -> List[str]:
    result = []
    seen = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def cache_key_for_macro_analysis(
    market: str,
    code: str,
    analysis_profile: str = MACRO_ANALYSIS_PROFILE,
    analysis_date: Optional[str] = None,
) -> str:
    return ":".join([
        str(market or "").upper(),
        str(code or "").upper(),
        analysis_date or date.today().isoformat(),
        analysis_profile,
    ])


def cache_row_is_valid(row: Optional[dict]) -> bool:
    if not row:
        return False
    expires_at = str(row.get("expires_at") or "").strip()
    if not expires_at:
        return False
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed > _now()


def _direction_label(value: str) -> str:
    return {
        "bullish": "偏多",
        "bearish": "偏空",
        "neutral": "中性",
        "avoid": "回避",
        "unknown": "信息不足",
    }.get(str(value or "").lower(), "信息不足")


def _instrument_type_for_market(market: str, code: str) -> str:
    text = f"{market} {code}".upper()
    if "ETF" in text or market.upper() == "A":
        return "ETF"
    return "股票"


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)] if str(value) else []


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
