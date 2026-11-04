from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class RiskProfile(Enum):
    CONSERVATIVE = ("conservative", "保守")
    BALANCED = ("balanced", "均衡")
    AGGRESSIVE = ("aggressive", "进取")

    @property
    def value_key(self) -> str:
        return self.value[0]

    @property
    def label(self) -> str:
        return self.value[1]

    @classmethod
    def from_input(cls, value: str | "RiskProfile") -> "RiskProfile":
        if isinstance(value, RiskProfile):
            return value
        text = str(value or "").strip().lower()
        for item in cls:
            if text in {item.value_key, item.label.lower()}:
                return item
        raise ValueError(f"不支持的风险偏好: {value}")


class OptionSide(Enum):
    BUY = ("buy", "买入")
    SELL = ("sell", "卖出")

    @property
    def value_key(self) -> str:
        return self.value[0]

    @property
    def label(self) -> str:
        return self.value[1]


class OptionContractType(Enum):
    CALL = ("call", "Call")
    PUT = ("put", "Put")

    @property
    def value_key(self) -> str:
        return self.value[0]

    @property
    def label(self) -> str:
        return self.value[1]


class MonitorEventSeverity(Enum):
    URGENT = ("urgent", "紧急")
    IMPORTANT = ("important", "重要")
    INFO = ("info", "提示")

    @property
    def value_key(self) -> str:
        return self.value[0]

    @property
    def label(self) -> str:
        return self.value[1]


@dataclass(frozen=True)
class DataQuality:
    status: str
    warnings: List[str] = field(default_factory=list)
    quote_time: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "warnings": list(self.warnings),
            "quote_time": self.quote_time,
        }


@dataclass(frozen=True)
class ContractDetail:
    side: OptionSide
    contract_type: OptionContractType
    option_code: str
    provider_code: str
    expiration_date: str
    strike: float
    suggested_price: Optional[float]
    quantity: int
    currency: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "side": self.side.value_key,
            "side_label": self.side.label,
            "contract_type": self.contract_type.value_key,
            "contract_type_label": self.contract_type.label,
            "option_code": self.option_code,
            "provider_code": self.provider_code,
            "expiration_date": self.expiration_date,
            "strike": self.strike,
            "suggested_price": self.suggested_price,
            "quantity": self.quantity,
            "currency": self.currency,
        }

    def to_display_row(self) -> Dict[str, Any]:
        return {
            "买卖方向": self.side.label,
            "期权类型": self.contract_type.label,
            "合约代码": self.option_code,
            "到期日": self.expiration_date,
            "行权价": self.strike,
            "建议价格": self.suggested_price,
            "数量": self.quantity,
            "币种": self.currency,
        }


@dataclass(frozen=True)
class OptionQuote:
    option_code: str
    provider_code: str
    contract_type: OptionContractType
    expiration_date: str
    strike: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    volume: Optional[float] = None
    open_interest: Optional[float] = None
    implied_volatility: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    quote_time: Optional[str] = None
    currency: str = "USD"

    @property
    def mid_price(self) -> Optional[float]:
        if self.bid is not None and self.ask is not None and self.ask >= self.bid:
            return round((float(self.bid) + float(self.ask)) / 2, 4)
        return self.last

    def to_dict(self) -> Dict[str, Any]:
        return {
            "option_code": self.option_code,
            "provider_code": self.provider_code,
            "contract_type": self.contract_type.value_key,
            "contract_type_label": self.contract_type.label,
            "expiration_date": self.expiration_date,
            "strike": self.strike,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "mid_price": self.mid_price,
            "volume": self.volume,
            "open_interest": self.open_interest,
            "implied_volatility": self.implied_volatility,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "vega": self.vega,
            "quote_time": self.quote_time,
            "currency": self.currency,
        }


@dataclass(frozen=True)
class UnderlyingSnapshot:
    market: str
    code: str
    name: str = ""
    price: Optional[float] = None
    currency: str = ""
    quote_time: Optional[str] = None
    signal_summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market,
            "code": self.code,
            "name": self.name,
            "price": self.price,
            "currency": self.currency,
            "quote_time": self.quote_time,
            "signal_summary": dict(self.signal_summary),
        }


@dataclass(frozen=True)
class MarketSnapshot:
    snapshot_id: str
    provider: str
    underlying: UnderlyingSnapshot
    option_quotes: List[OptionQuote]
    data_quality: DataQuality
    raw_payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "provider": self.provider,
            "underlying": self.underlying.to_dict(),
            "option_quotes": [quote.to_dict() for quote in self.option_quotes],
            "data_quality": self.data_quality.to_dict(),
            "raw_payload": dict(self.raw_payload),
        }


@dataclass(frozen=True)
class StrategyCandidate:
    candidate_id: str
    run_id: str
    market: str
    code: str
    strategy_key: str
    strategy_name: str
    score: float
    recommendation_status: str
    fit_reason: str
    contract_details: List[ContractDetail]
    risk_metrics: Dict[str, Any]
    order_suggestion: Dict[str, Any]
    warnings: List[str]
    data_quality: DataQuality
    created_at: datetime
    option_score: Optional[float] = None
    macro_score: Optional[float] = None
    composite_score: Optional[float] = None
    macro_direction: str = ""
    news_impact: str = ""
    hot_sector_mark: str = ""
    main_force_risk_level: str = ""
    macro_summary: str = ""
    macro_positive_factors: List[str] = field(default_factory=list)
    macro_risk_factors: List[str] = field(default_factory=list)
    macro_factors: List[str] = field(default_factory=list)
    macro_source_urls: List[str] = field(default_factory=list)
    macro_data_gaps: List[str] = field(default_factory=list)
    macro_evidence_links: List[Dict[str, Any]] = field(default_factory=list)
    macro_factor_citations: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "candidate_id": self.candidate_id,
            "run_id": self.run_id,
            "market": self.market,
            "code": self.code,
            "strategy_key": self.strategy_key,
            "策略名称": self.strategy_name,
            "评分": self.score,
            "期权评分": self.option_score if self.option_score is not None else self.score,
            "recommendation_status": self.recommendation_status,
            "适用理由": self.fit_reason,
            "合约明细": [item.to_display_row() for item in self.contract_details],
            "risk_metrics": dict(self.risk_metrics),
            "order_suggestion": dict(self.order_suggestion),
            "warnings": list(self.warnings),
            "数据质量": self.data_quality.to_dict(),
            "created_at": self.created_at.isoformat(),
        }
        if self.macro_score is not None:
            payload.update({
                "宏观分析评分": self.macro_score,
                "综合评分": self.composite_score,
                "宏观方向": self.macro_direction,
                "新闻影响": self.news_impact,
                "热点匹配": self.hot_sector_mark,
                "主力资金风险": self.main_force_risk_level,
                "宏观摘要": self.macro_summary,
                "关键利好因素": list(self.macro_positive_factors),
                "关键风险因素": list(self.macro_risk_factors),
                "宏观/政策因素": list(self.macro_factors),
                "信息来源": list(self.macro_source_urls),
                "数据缺失原因": list(self.macro_data_gaps),
                "引用来源": [dict(item) for item in self.macro_evidence_links],
                "因素引用": {
                    str(key): [dict(item) for item in value]
                    for key, value in self.macro_factor_citations.items()
                },
            })
        return payload


@dataclass(frozen=True)
class OptionEvaluationRequest:
    market: str
    code: str
    risk_profile: RiskProfile
    capital: Optional[float] = None
    max_loss: Optional[float] = None
    planned_holding_days: Optional[int] = None
    strategy_scope: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class OptionEvaluationResult:
    run_id: str
    market: str
    code: str
    status: str
    risk_profile: RiskProfile
    candidates: List[StrategyCandidate]
    warnings: List[str] = field(default_factory=list)
    data_quality: DataQuality = field(default_factory=lambda: DataQuality(status="unknown"))
    macro_analysis: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "run_id": self.run_id,
            "market": self.market,
            "code": self.code,
            "status": self.status,
            "risk_profile": self.risk_profile.value_key,
            "risk_profile_label": self.risk_profile.label,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "warnings": list(self.warnings),
            "data_quality": self.data_quality.to_dict(),
        }
        if self.macro_analysis is not None:
            payload["macro_analysis"] = (
                self.macro_analysis.to_dict()
                if hasattr(self.macro_analysis, "to_dict")
                else self.macro_analysis
            )
        return payload


@dataclass(frozen=True)
class OrderPlan:
    plan_id: str
    candidate_id: str
    status: str
    contract_details: List[ContractDetail]
    order_suggestion: Dict[str, Any]
    created_at: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "candidate_id": self.candidate_id,
            "status": self.status,
            "contract_details": [item.to_dict() for item in self.contract_details],
            "order_suggestion": dict(self.order_suggestion),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class FillRecord:
    plan_id: str
    contract_details: List[ContractDetail]
    filled_price: float
    quantity: int
    filled_at: str
    fee: Optional[float] = None


@dataclass(frozen=True)
class TrackedPosition:
    position_id: str
    plan_id: str
    status: str
    contract_details: List[ContractDetail]
    opened_at: str
    filled_price: float
    quantity: int
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    max_holding_days: Optional[int] = None


@dataclass(frozen=True)
class MonitorEvent:
    event_id: str
    position_id: str
    severity: MonitorEventSeverity
    event_type: str
    message: str
    created_at: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "position_id": self.position_id,
            "severity": self.severity.value_key,
            "severity_label": self.severity.label,
            "event_type": self.event_type,
            "message": self.message,
            "created_at": self.created_at.isoformat(),
        }
