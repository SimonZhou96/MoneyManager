#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库驱动的筛选规则引擎。

职责边界：
- RuleRepository 只负责读取数据库配置。
- RuleRegistry 只负责把受信任 implementation 映射为 Python 对象。
- RuleExpressionEvaluator 只负责解释 JSON DSL。
- RuleEngine 负责执行单只股票的原子规则并返回 StockFilterResult。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

if __package__:
    from .filters import (
        AvgDailyVolumeFilter,
        Filter,
        FilterContext,
        FilterOutput,
        FilterResult,
        MarketCapFilter,
        PEFilter,
        PriceFilter,
        ProfitabilityFilter,
        StockFilterResult,
        StockInfo,
    )
    from .macro_strategies import (
        CompanyEventHotNewsStrategizer,
        CompanyEventHotSectorStrategizer,
        MarketIntelMacroScoreStrategizer,
    )
    from .potential_analysis.strategizer import (
        EnterprisePotentialAnalysisStrategizer,
        MacroFactorAnalysisStrategizer,
    )
    from .strategizers import (
        DailyPctChangeBandStrategizer,
        EMABreakoutStrategizer,
        EnergyPhaseClassifier,
        RSIOverboughtStrategizer,
        RSIOversoldStrategizer,
        Strategizer,
        StrategizerOutput,
        TechnicalPatternStrategizer,
        TodayVolumeExceedsPrior3MaxStrategizer,
        MarketTemperatureStrategizer,
        ZuoYiStrategizer,
    )
else:
    from filters import (
        AvgDailyVolumeFilter,
        Filter,
        FilterContext,
        FilterOutput,
        FilterResult,
        MarketCapFilter,
        PEFilter,
        PriceFilter,
        ProfitabilityFilter,
        StockFilterResult,
        StockInfo,
    )
    from macro_strategies import (
        CompanyEventHotNewsStrategizer,
        CompanyEventHotSectorStrategizer,
        MarketIntelMacroScoreStrategizer,
    )
    from potential_analysis.strategizer import (
        EnterprisePotentialAnalysisStrategizer,
        MacroFactorAnalysisStrategizer,
    )
    from strategizers import (
        DailyPctChangeBandStrategizer,
        EMABreakoutStrategizer,
        EnergyPhaseClassifier,
        RSIOverboughtStrategizer,
        RSIOversoldStrategizer,
        Strategizer,
        StrategizerOutput,
        TechnicalPatternStrategizer,
        TodayVolumeExceedsPrior3MaxStrategizer,
        MarketTemperatureStrategizer,
        ZuoYiStrategizer,
    )


RULE_TYPE_FILTER = "filter"
RULE_TYPE_STRATEGY = "strategy"
RUNTIME_DEPENDENCY_RESOLVER_CACHE_KEY = "runtime_dependency_resolver"
RULE_EXECUTION_CALLBACK_CACHE_KEY = "rule_execution_callback"
SIGNAL_GROUP_BULLISH = "bullish"
SIGNAL_GROUP_REBOUND = "rebound"
SIGNAL_GROUP_ZUOYI_BULLISH = "zuoyi_bullish"
SIGNAL_GROUP_LABELS = {
    SIGNAL_GROUP_BULLISH: "看涨",
    SIGNAL_GROUP_REBOUND: "准备反弹",
    SIGNAL_GROUP_ZUOYI_BULLISH: "左一看涨",
}


@dataclass(frozen=True)
class RuleMetadata:
    """一条原子规则配置。"""

    market: str
    rule_key: str
    rule_name: str
    rule_type: str
    implementation: str
    strategy_category: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    display_order: int = 0
    description: str = ""

    @classmethod
    def from_row(cls, row: dict) -> "RuleMetadata":
        params = row.get("params_json") or {}
        if isinstance(params, str):
            params = json.loads(params) if params else {}
        return cls(
            market=str(row.get("market") or ""),
            rule_key=str(row.get("rule_key") or ""),
            rule_name=str(row.get("rule_name") or ""),
            rule_type=str(row.get("rule_type") or ""),
            implementation=str(row.get("implementation") or ""),
            strategy_category=str(row.get("strategy_category") or ""),
            params=params if isinstance(params, dict) else {},
            enabled=bool(row.get("enabled")),
            display_order=int(row.get("display_order") or 0),
            description=str(row.get("description") or ""),
        )


@dataclass(frozen=True)
class RuleChainConfig:
    """一条市场级规则链配置。"""

    market: str
    timeframe: str
    chain_key: str
    chain_name: str
    expression: Dict[str, Any]
    enabled: bool = True
    priority: int = 100
    description: str = ""

    @classmethod
    def from_row(cls, row: dict) -> "RuleChainConfig":
        expression = row.get("expression_json") or {}
        if isinstance(expression, str):
            expression = json.loads(expression) if expression else {}
        return cls(
            market=str(row.get("market") or ""),
            timeframe=str(row.get("timeframe") or "*"),
            chain_key=str(row.get("chain_key") or ""),
            chain_name=str(row.get("chain_name") or ""),
            expression=expression if isinstance(expression, dict) else {},
            enabled=bool(row.get("enabled")),
            priority=int(row.get("priority") or 100),
            description=str(row.get("description") or ""),
        )


class RuleRepository:
    """从 MarketDatabase 加载规则配置。"""

    def __init__(self, db: Any):
        self.db = db

    def load_metadata(self, market: str) -> List[RuleMetadata]:
        rows = self.db.get_screening_rule_metadata(market)
        return sorted(
            [RuleMetadata.from_row(row) for row in rows],
            key=lambda item: item.display_order,
        )

    def load_active_chain(self, market: str, timeframe: str = "*") -> RuleChainConfig:
        timeframe = str(timeframe or "*")
        row = self.db.get_active_screening_rule_chain(market, timeframe)
        if not row:
            raise ValueError(f"未找到启用的规则链: market={market}, timeframe={timeframe}")
        chain = RuleChainConfig.from_row(row)
        if not chain.enabled:
            raise ValueError(f"规则链未启用: market={market}, timeframe={timeframe}, chain_key={chain.chain_key}")
        if not chain.expression:
            raise ValueError(f"规则链表达式为空: market={market}, timeframe={timeframe}, chain_key={chain.chain_key}")
        return chain

    def load_chain(self, market: str, chain_key: str, timeframe: str = "*") -> RuleChainConfig:
        timeframe = str(timeframe or "*")
        row = self.db.get_screening_rule_chain(market, chain_key, timeframe)
        if not row:
            raise ValueError(f"未找到规则链: market={market}, timeframe={timeframe}, chain_key={chain_key}")
        chain = RuleChainConfig.from_row(row)
        if not chain.expression:
            raise ValueError(f"规则链表达式为空: market={market}, timeframe={timeframe}, chain_key={chain.chain_key}")
        return chain

    def load_chains(self, market: str, timeframe: Optional[str] = None) -> List[RuleChainConfig]:
        rows = self.db.list_screening_rule_chains(market, timeframe)
        return [RuleChainConfig.from_row(row) for row in rows]


class RuleRegistry:
    """受信任规则实现白名单。"""

    def __init__(self):
        self._filter_factories: Dict[str, Callable[[dict], Filter]] = {}
        self._strategy_factories: Dict[str, Callable[[dict], Strategizer]] = {}
        self._entries: List[RuleMetadata] = []

    def register_filter(self, implementation: str, factory: Callable[[dict], Filter]) -> "RuleRegistry":
        self._filter_factories[implementation] = factory
        return self

    def register_strategy(self, implementation: str, factory: Callable[[dict], Strategizer]) -> "RuleRegistry":
        self._strategy_factories[implementation] = factory
        return self

    def register(self, metadata: RuleMetadata) -> "RuleRegistry":
        """注册一条规则元数据。"""
        self._entries.append(metadata)
        return self

    def list_entries(self) -> List[RuleMetadata]:
        """返回所有已注册的规则元数据。"""
        return list(self._entries)

    def create(self, metadata: RuleMetadata) -> Filter | Strategizer:
        params = dict(metadata.params or {})
        if metadata.rule_type == RULE_TYPE_FILTER:
            factory = self._filter_factories.get(metadata.implementation)
        elif metadata.rule_type == RULE_TYPE_STRATEGY:
            factory = self._strategy_factories.get(metadata.implementation)
        else:
            raise ValueError(f"不支持的规则类型: {metadata.rule_type}")
        if factory is None:
            raise ValueError(f"未注册的规则实现: {metadata.implementation}")
        return factory(params)

    @classmethod
    def default(cls) -> "RuleRegistry":
        registry = cls()
        registry.register_filter(
            "MarketCapFilter",
            lambda params: MarketCapFilter(
                min_cap=params.get("min_cap"),
                max_cap=params.get("max_cap"),
                min_exclusive=bool(params.get("min_exclusive", False)),
            ),
        )
        registry.register_filter(
            "AvgDailyVolumeFilter",
            lambda params: AvgDailyVolumeFilter(
                min_volume=params.get("min_volume"),
                max_volume=params.get("max_volume"),
                lookback_days=params.get("lookback_days"),
                metric=params.get("metric", "volume"),
                min_exclusive=bool(params.get("min_exclusive", False)),
            ),
        )
        registry.register_filter(
            "PriceFilter",
            lambda params: PriceFilter(
                min_price=params.get("min_price"),
                max_price=params.get("max_price"),
                min_exclusive=bool(params.get("min_exclusive", False)),
            ),
        )
        registry.register_filter(
            "PEFilter",
            lambda params: PEFilter(
                min_pe=params.get("min_pe"),
                max_pe=params.get("max_pe"),
                allow_negative=bool(params.get("allow_negative", False)),
                min_exclusive=bool(params.get("min_exclusive", False)),
            ),
        )
        registry.register_filter(
            "ProfitabilityFilter",
            lambda params: ProfitabilityFilter(
                require_profitable=bool(params.get("require_profitable", True)),
            ),
        )

        registry.register_strategy(
            "MarketTemperatureStrategizer",
            lambda params: MarketTemperatureStrategizer(
                min_score=float(params.get("min_score", 50)),
            ),
        )
        registry.register_strategy(
            "ZuoYiStrategizer",
            lambda params: ZuoYiStrategizer(
                signal_window=int(params.get("signal_window", 15)),
                include_bullish=bool(params.get("include_bullish", True)),
                include_bearish=bool(params.get("include_bearish", True)),
            ),
        )
        registry.register_strategy(
            "EMABreakoutStrategizer",
            lambda params: EMABreakoutStrategizer(
                ema_short=int(params.get("ema_short", 10)),
                ema_long=int(params.get("ema_long", 150)),
            ),
        )
        registry.register_strategy(
            "RSIOversoldStrategizer",
            lambda params: RSIOversoldStrategizer(
                period=int(params.get("period", 14)),
                threshold=float(params.get("threshold", 30.0)),
            ),
        )
        registry.register_strategy(
            "RSIOverboughtStrategizer",
            lambda params: RSIOverboughtStrategizer(
                period=int(params.get("period", 14)),
                threshold=float(params.get("threshold", 70.0)),
            ),
        )
        registry.register_strategy(
            "TodayVolumeExceedsPrior3MaxStrategizer",
            lambda params: TodayVolumeExceedsPrior3MaxStrategizer(),
        )
        registry.register_strategy(
            "DailyDrop6To65Strategizer",
            lambda params: DailyPctChangeBandStrategizer(
                pct_min=float(params.get("pct_min", -6.5)),
                pct_max=float(params.get("pct_max", -6.0)),
                name="DailyDrop6To65Strategizer",
            ),
        )
        registry.register_strategy(
            "DailyRise4To45Strategizer",
            lambda params: DailyPctChangeBandStrategizer(
                pct_min=float(params.get("pct_min", 4.0)),
                pct_max=float(params.get("pct_max", 4.5)),
                name="DailyRise4To45Strategizer",
            ),
        )
        registry.register_strategy(
            "TechnicalPatternStrategizer",
            lambda params: TechnicalPatternStrategizer(
                pattern_key=str(params.get("pattern_key") or ""),
                **{
                    key: value
                    for key, value in params.items()
                    if key not in {"pattern_key", "direction", "pattern_label", "display_group", "signal_group"}
                },
            ),
        )
        registry.register_strategy(
            "CompanyEventHotSectorStrategizer",
            lambda params: CompanyEventHotSectorStrategizer(),
        )
        registry.register_strategy(
            "CompanyEventHotNewsStrategizer",
            lambda params: CompanyEventHotNewsStrategizer(),
        )
        registry.register_strategy(
            "MarketIntelMacroScoreStrategizer",
            lambda params: MarketIntelMacroScoreStrategizer(
                threshold=params.get("threshold", 60),
                refresh_policy=params.get("refresh_policy", "cache_or_refresh"),
                technical_weight=params.get("technical_weight", 0.6),
                macro_weight=params.get("macro_weight", 0.4),
            ),
        )
        registry.register_strategy(
            "MacroFactorAnalysisStrategizer",
            lambda params: MacroFactorAnalysisStrategizer(
                min_factors=int(params.get("min_factors", 5)),
            ),
        )
        registry.register_strategy(
            "EnterprisePotentialAnalysisStrategizer",
            lambda params: EnterprisePotentialAnalysisStrategizer(
                threshold=float(params.get("threshold", 70)),
            ),
        )
        registry.register_strategy(
            "EnergyPhaseClassifier",
            lambda params: EnergyPhaseClassifier(
                ma_period=int(params.get("ma_period", 20)),
                pe_threshold=float(params.get("pe_threshold", 100.0)),
                ke_threshold=float(params.get("ke_threshold", 4.0)),
                epr_release_threshold=float(params.get("epr_release_threshold", 0.1)),
                ke_decay_exhaustion=float(params.get("ke_decay_exhaustion", 0.5)),
                ke_decay_peak=float(params.get("ke_decay_peak", 0.8)),
                consistency_window=int(params.get("consistency_window", 10)),
                delta_window=int(params.get("delta_window", 5)),
                lookback_pe_days=int(params.get("lookback_pe_days", 3)),
                min_rows=int(params.get("min_rows", 30)),
            ),
        )

        # ── 持有层宏观规则（6 个新增） ──────────────────────
        registry.register(RuleMetadata(
            market="*", rule_key="credit_risk_regime",
            rule_name="信用风险环境", rule_type=RULE_TYPE_STRATEGY,
            implementation="CreditRiskRegimeStrategizer",
            strategy_category="macro",
            params={}, enabled=True, display_order=200,
            description="判断市场信用风险是否上升（HYG/LQD/VIX ETF代理）",
        ))
        registry.register(RuleMetadata(
            market="*", rule_key="market_breadth_regime",
            rule_name="市场宽度环境", rule_type=RULE_TYPE_STRATEGY,
            implementation="MarketBreadthRegimeStrategizer",
            strategy_category="macro",
            params={}, enabled=True, display_order=210,
            description="判断指数上涨是否健康扩散到多数股票",
        ))
        registry.register(RuleMetadata(
            market="*", rule_key="liquidity_nowcast",
            rule_name="资金流动性即时报", rule_type=RULE_TYPE_STRATEGY,
            implementation="LiquidityNowcastStrategizer",
            strategy_category="macro",
            params={}, enabled=True, display_order=220,
            description="判断资金环境是否支持继续持有",
        ))
        registry.register(RuleMetadata(
            market="*", rule_key="earnings_revision_momentum",
            rule_name="盈利预期修正", rule_type=RULE_TYPE_STRATEGY,
            implementation="EarningsRevisionMomentumStrategizer",
            strategy_category="macro",
            params={}, enabled=True, display_order=230,
            description="判断公司未来盈利预期是否改善（Tavily+LLM代理）",
        ))
        registry.register(RuleMetadata(
            market="*", rule_key="policy_event_risk",
            rule_name="政策事件风险", rule_type=RULE_TYPE_STRATEGY,
            implementation="PolicyEventRiskStrategizer",
            strategy_category="macro",
            params={}, enabled=True, display_order=240,
            description="识别宏观政策/行业政策/监管事件/地缘事件影响",
        ))
        registry.register(RuleMetadata(
            market="*", rule_key="commodity_shock",
            rule_name="大宗商品冲击", rule_type=RULE_TYPE_STRATEGY,
            implementation="CommodityShockStrategizer",
            strategy_category="macro",
            params={}, enabled=True, display_order=250,
            description="识别大宗商品价格变化对资源/周期/制造/消费行业影响",
        ))

        # ── EntryScore 聚合器（5 个评分层 Strategy） ─────────
        registry.register(RuleMetadata(
            market="*", rule_key="trend_structure",
            rule_name="趋势结构", rule_type=RULE_TYPE_STRATEGY,
            implementation="TrendStructureStrategizer",
            strategy_category="scoring",
            params={}, enabled=True, display_order=300,
            description="[评分聚合] 趋势结构模块（EMA排列/左一/MA位置）",
        ))
        registry.register(RuleMetadata(
            market="*", rule_key="momentum_state",
            rule_name="动量状态", rule_type=RULE_TYPE_STRATEGY,
            implementation="MomentumStateStrategizer",
            strategy_category="scoring",
            params={}, enabled=True, display_order=310,
            description="[评分聚合] 动量状态模块（RSI/MACD/KDJ/能量相位）",
        ))
        registry.register(RuleMetadata(
            market="*", rule_key="volume_confirmation",
            rule_name="成交确认", rule_type=RULE_TYPE_STRATEGY,
            implementation="VolumeConfirmationStrategizer",
            strategy_category="scoring",
            params={}, enabled=True, display_order=320,
            description="[评分聚合] 成交确认模块（放量/量价配合）",
        ))
        registry.register(RuleMetadata(
            market="*", rule_key="breakout_quality",
            rule_name="突破质量", rule_type=RULE_TYPE_STRATEGY,
            implementation="BreakoutQualityStrategizer",
            strategy_category="scoring",
            params={}, enabled=True, display_order=330,
            description="[评分聚合] 突破质量模块（ATR突破/新高/形态有效性）",
        ))
        registry.register(RuleMetadata(
            market="*", rule_key="volatility_risk",
            rule_name="波动风险", rule_type=RULE_TYPE_STRATEGY,
            implementation="VolatilityRiskStrategizer",
            strategy_category="scoring",
            params={}, enabled=True, display_order=340,
            description="[评分聚合] 波动风险模块（布林带宽/回撤/日内振幅）",
        ))
        return registry


class RuntimeDependencyResolver:
    """Resolve rule-declared runtime dependencies lazily and once per context."""

    def __init__(self, loaders: Optional[Dict[str, Callable[[FilterContext], Any]]] = None):
        self._loaders = dict(loaders or {})

    @staticmethod
    def cache_key(name: str) -> str:
        return f"runtime_dependency:{name}"

    def ensure(self, names: Iterable[str], context: FilterContext) -> None:
        for name in names:
            dependency = str(name or "").strip()
            if not dependency:
                continue
            cache_key = self.cache_key(dependency)
            if cache_key in context._cache:
                continue
            loader = self._loaders.get(dependency)
            if not callable(loader):
                raise RuntimeError(f"未配置规则运行时依赖: {dependency}")
            context.set_cache(cache_key, loader(context))


class RuleExecutionContext:
    """单只股票的规则执行状态。"""

    def __init__(
        self,
        stock: StockInfo,
        filter_context: FilterContext,
        metadata_by_key: Dict[str, RuleMetadata],
        registry: RuleRegistry,
    ):
        self.stock = stock
        self.filter_context = filter_context
        self.metadata_by_key = metadata_by_key
        self.registry = registry
        self.outputs_by_key: Dict[str, FilterOutput] = {}
        self.truth_by_key: Dict[str, bool] = {}
        self.ordered_outputs: List[FilterOutput] = []

    def execute(self, rule_key: str) -> bool:
        if rule_key in self.truth_by_key:
            return self.truth_by_key[rule_key]

        metadata = self.metadata_by_key.get(rule_key)
        if metadata is None or not metadata.enabled:
            self.truth_by_key[rule_key] = False
            return False

        callback = self.filter_context.get_cache(RULE_EXECUTION_CALLBACK_CACHE_KEY)
        if callable(callback):
            callback({
                "rule_key": metadata.rule_key,
                "rule_name": metadata.rule_name,
                "rule_type": metadata.rule_type,
                "strategy_category": metadata.strategy_category,
            })

        try:
            implementation = self.registry.create(metadata)
            required_dependencies = getattr(implementation, "required_dependencies", ())
            resolver = self.filter_context.get_cache(RUNTIME_DEPENDENCY_RESOLVER_CACHE_KEY)
            if required_dependencies:
                if not isinstance(resolver, RuntimeDependencyResolver):
                    raise RuntimeError("规则需要运行时依赖，但当前任务未配置依赖解析器")
                resolver.ensure(required_dependencies, self.filter_context)
            if metadata.rule_type == RULE_TYPE_FILTER:
                output = implementation.apply(self.stock, self.filter_context)
                truth = output.result in (FilterResult.PASS, FilterResult.SKIP)
            elif metadata.rule_type == RULE_TYPE_STRATEGY:
                strategy_output = implementation.apply(self.stock, self.filter_context)
                output = self._strategy_to_filter_output(strategy_output)
                truth = output.result in (FilterResult.PASS, FilterResult.SKIP)
            else:
                output = FilterOutput(
                    filter_name=metadata.implementation,
                    result=FilterResult.ERROR,
                    reason=f"不支持的规则类型: {metadata.rule_type}",
                )
                truth = False
        except Exception as exc:
            output = FilterOutput(
                filter_name=metadata.implementation or rule_key,
                result=FilterResult.ERROR,
                reason=str(exc),
                details={"error": True, "rule_key": rule_key},
            )
            truth = False

        output.details = dict(output.details or {})
        output.details.setdefault("rule_key", rule_key)
        self.outputs_by_key[rule_key] = output
        self.truth_by_key[rule_key] = truth
        self.ordered_outputs.append(output)
        return truth

    @staticmethod
    def _strategy_to_filter_output(output: StrategizerOutput) -> FilterOutput:
        result_value = str(getattr(output, "result", "") or "").lower()
        if result_value == "skip":
            result = FilterResult.SKIP
        elif result_value == "pass":
            result = FilterResult.PASS
        elif result_value == "fail":
            result = FilterResult.FAIL
        elif result_value == "error":
            result = FilterResult.ERROR
        else:
            result = FilterResult.PASS if output.satisfied else FilterResult.FAIL
        return FilterOutput(
            filter_name=output.name,
            result=result,
            reason=output.reason or "",
            details=dict(output.details) if output.details else {},
        )


class RuleExpressionEvaluator:
    """解释规则链 JSON DSL。"""

    def __init__(self, metadata_by_key: Dict[str, RuleMetadata]):
        self.metadata_by_key = metadata_by_key

    def evaluate(self, expression: dict, context: RuleExecutionContext) -> bool:
        if isinstance(expression, str):
            return context.execute(expression)
        if not isinstance(expression, dict) or not expression:
            return False

        if "ref" in expression:
            return context.execute(str(expression["ref"]))

        if "and" in expression:
            items = self._as_list(expression["and"])
            if not items:
                return True
            for item in items:
                if not self.evaluate(item, context):
                    return False
            return True

        if "any" in expression:
            items = self._as_list(expression["any"])
            if not items:
                return False
            for item in items:
                if self.evaluate(item, context):
                    return True
            return False

        if "all_enabled" in expression:
            rule_keys = self._enabled_keys(expression["all_enabled"])
            if not rule_keys:
                return True
            for rule_key in rule_keys:
                if not context.execute(rule_key):
                    return False
            return True

        if "any_enabled" in expression:
            rule_keys = self._enabled_keys(expression["any_enabled"])
            if not rule_keys:
                return False
            for rule_key in rule_keys:
                if context.execute(rule_key):
                    return True
            return False

        return False

    def collect_rule_keys(self, expression: dict) -> Set[str]:
        if isinstance(expression, str):
            return {expression}
        if not isinstance(expression, dict) or not expression:
            return set()
        if "ref" in expression:
            return {str(expression["ref"])}
        keys: Set[str] = set()
        for field in ("and", "any"):
            if field in expression:
                for item in self._as_list(expression[field]):
                    keys.update(self.collect_rule_keys(item))
        for field in ("all_enabled", "any_enabled"):
            if field in expression:
                keys.update(str(item) for item in self._as_list(expression[field]))
        return keys

    def _enabled_keys(self, rule_keys: Iterable[Any]) -> List[str]:
        enabled = []
        for item in self._as_list(rule_keys):
            rule_key = str(item)
            metadata = self.metadata_by_key.get(rule_key)
            if metadata is not None and metadata.enabled:
                enabled.append(rule_key)
        return enabled

    @staticmethod
    def _as_list(value: Any) -> List[Any]:
        return value if isinstance(value, list) else []


class RuleEngine:
    """面向筛选流程的规则引擎门面。"""

    KLINE_IMPLEMENTATIONS = {
        "AvgDailyVolumeFilter",
        "PriceFilter",
        "ZuoYiStrategizer",
        "EMABreakoutStrategizer",
        "EnergyPhaseClassifier",
        "RSIOversoldStrategizer",
        "RSIOverboughtStrategizer",
        "TodayVolumeExceedsPrior3MaxStrategizer",
        "DailyDrop6To65Strategizer",
        "DailyRise4To45Strategizer",
        "TechnicalPatternStrategizer",
    }
    SIGNAL_ANALYSIS_IMPLEMENTATIONS = {
        "CompanyEventHotSectorStrategizer",
        "CompanyEventHotNewsStrategizer",
    }
    MARKET_INTEL_MACRO_IMPLEMENTATIONS = {
        "MarketIntelMacroScoreStrategizer",
    }
    ENTERPRISE_POTENTIAL_IMPLEMENTATIONS = {
        "MacroFactorAnalysisStrategizer",
        "EnterprisePotentialAnalysisStrategizer",
    }

    def __init__(
        self,
        metadata: List[RuleMetadata],
        chain_config: RuleChainConfig,
        registry: Optional[RuleRegistry] = None,
    ):
        self.metadata = sorted(metadata, key=lambda item: item.display_order)
        self.chain_config = chain_config
        self.registry = registry or RuleRegistry.default()
        self.metadata_by_key = {item.rule_key: item for item in self.metadata}
        self.evaluator = RuleExpressionEvaluator(self.metadata_by_key)
        self.referenced_rule_keys = self.evaluator.collect_rule_keys(chain_config.expression)

    def has_rules(self) -> bool:
        return bool(self.referenced_rule_keys)

    def requires_signal_analysis(self) -> bool:
        return self._references_enabled_implementations(self.SIGNAL_ANALYSIS_IMPLEMENTATIONS)

    def requires_market_intel_macro_score(self) -> bool:
        return self._references_enabled_implementations(self.MARKET_INTEL_MACRO_IMPLEMENTATIONS)

    def requires_enterprise_potential(self) -> bool:
        return self._references_enabled_implementations(self.ENTERPRISE_POTENTIAL_IMPLEMENTATIONS)

    def requires_macro_analysis(self) -> bool:
        return (self.requires_signal_analysis()
                or self.requires_market_intel_macro_score()
                or self.requires_enterprise_potential())

    def _references_enabled_implementations(self, implementations: Set[str]) -> bool:
        for rule_key in self.referenced_rule_keys:
            metadata = self.metadata_by_key.get(rule_key)
            if (
                metadata
                and metadata.enabled
                and metadata.rule_type == RULE_TYPE_STRATEGY
                and metadata.implementation in implementations
            ):
                return True
        return False

    def requires_kline(self) -> bool:
        for rule_key in self.referenced_rule_keys:
            metadata = self.metadata_by_key.get(rule_key)
            if metadata and metadata.enabled and metadata.implementation in self.KLINE_IMPLEMENTATIONS:
                return True
        return False

    def bullish_technical_rule_keys(self) -> List[str]:
        """Return enabled technical rule keys explicitly marked as bullish."""
        keys = []
        for metadata in self.metadata:
            if not metadata.enabled:
                continue
            if metadata.rule_type != RULE_TYPE_STRATEGY:
                continue
            if metadata.strategy_category != "technical":
                continue
            direction = str((metadata.params or {}).get("direction") or "").lower()
            if direction == "bullish":
                keys.append(metadata.rule_key)
        return keys

    def evaluate_bullish_technical_rules(
        self,
        stock: StockInfo,
        filter_context: FilterContext,
    ) -> StockFilterResult:
        """Evaluate every enabled bullish technical rule without chain short-circuiting."""
        execution = RuleExecutionContext(
            stock=stock,
            filter_context=filter_context,
            metadata_by_key=self.metadata_by_key,
            registry=self.registry,
        )
        bullish_rule_keys = self.bullish_technical_rule_keys()
        for rule_key in bullish_rule_keys:
            execution.execute(rule_key)

        matched_labels = []
        categorized_matches = []
        group_counts = {
            SIGNAL_GROUP_BULLISH: 0,
            SIGNAL_GROUP_REBOUND: 0,
            SIGNAL_GROUP_ZUOYI_BULLISH: 0,
        }
        for rule_key in bullish_rule_keys:
            output = execution.outputs_by_key.get(rule_key)
            if output is None:
                continue
            if output.result != FilterResult.PASS:
                continue
            details = output.details if isinstance(output.details, dict) else {}
            metadata = self.metadata_by_key.get(rule_key)
            signal_group = self._signal_group_for_metadata(metadata)
            label = self._condition_label_for_match(output, metadata)
            if not label:
                continue
            group_label = SIGNAL_GROUP_LABELS.get(signal_group, SIGNAL_GROUP_LABELS[SIGNAL_GROUP_BULLISH])
            prefixed_label = f"{group_label}:{label}"
            if prefixed_label not in matched_labels:
                matched_labels.append(prefixed_label)
                group_counts[signal_group] = group_counts.get(signal_group, 0) + 1
                categorized_matches.append({
                    "rule_key": rule_key,
                    "rule_name": metadata.rule_name if metadata else rule_key,
                    "label": label,
                    "display_label": prefixed_label,
                    "signal_group": signal_group,
                    "signal_group_label": group_label,
                    "filter_name": output.filter_name,
                    "reason": output.reason or "",
                    "details": dict(details),
                })
            details["signal_group"] = signal_group
            details["signal_group_label"] = group_label

        result = StockFilterResult(
            stock=stock,
            passed=bool(matched_labels),
            filter_outputs=execution.ordered_outputs,
        )
        result.bullish_match_count = group_counts.get(SIGNAL_GROUP_BULLISH, 0)
        result.rebound_match_count = group_counts.get(SIGNAL_GROUP_REBOUND, 0)
        result.zuoyi_bullish_match_count = group_counts.get(SIGNAL_GROUP_ZUOYI_BULLISH, 0)
        result.total_match_count = len(matched_labels)
        result.bullish_condition_labels = matched_labels
        result.categorized_condition_matches = categorized_matches
        return result

    @staticmethod
    def _signal_group_for_metadata(metadata: Optional[RuleMetadata]) -> str:
        if metadata is None:
            return SIGNAL_GROUP_BULLISH
        signal_group = str((metadata.params or {}).get("signal_group") or SIGNAL_GROUP_BULLISH).strip().lower()
        if signal_group in SIGNAL_GROUP_LABELS:
            return signal_group
        return SIGNAL_GROUP_BULLISH

    @staticmethod
    def _condition_label_for_match(output: FilterOutput, metadata: Optional[RuleMetadata]) -> str:
        details = output.details if isinstance(output.details, dict) else {}
        label = str(details.get("pattern_label") or "").strip()
        if not label and output.filter_name == "ZuoYiStrategizer":
            direction = str(details.get("direction") or "")
            if "bullish" in direction.split("|"):
                label = "左一战法-看涨"
        if not label and metadata is not None:
            label = str((metadata.params or {}).get("pattern_label") or metadata.rule_name or "").strip()
        if not label:
            label = str(details.get("label") or output.filter_name or "").strip()
        return label

    def evaluate_macro_rules_for_top20(
        self,
        stock: StockInfo,
        filter_context: FilterContext,
        market_cache: Optional[Any] = None,
    ) -> StockFilterResult:
        """Evaluate macro rules for Top20 selected stocks only.

        These rules (macro_factor_analysis, enterprise_potential_analysis,
        company_event_hot_sector_link, company_event_hot_news_link) have
        strategy_category='macro' and are excluded from
        evaluate_bullish_technical_rules(). They run after the Top20
        technical selection to provide the final macro scoring layer.

        When market_cache is provided, the 4 pre-computed market-level
        rules (credit_risk_regime, market_breadth_regime, liquidity_nowcast,
        policy_event_risk, commodity_shock) from MarketTemperature are
        injected as FilterOutput objects, so they appear in the caller's
        filter_details without per-stock re-evaluation. earnings_revision_momentum
        is stock-level and NOT injected here (handled in Phase 3).
        """
        execution = RuleExecutionContext(
            stock=stock,
            filter_context=filter_context,
            metadata_by_key=self.metadata_by_key,
            registry=self.registry,
        )
        for rule_key in (
            'macro_factor_analysis',
            'enterprise_potential_analysis',
            'company_event_hot_sector_link',
            'company_event_hot_news_link',
        ):
            metadata = self.metadata_by_key.get(rule_key)
            if metadata is not None and metadata.enabled:
                execution.execute(rule_key)

        # 新增：从 MarketCache 注入市场级规则结果（不逐只调用）
        if market_cache is not None:
            try:
                market_temp = market_cache.get_or_compute(filter_context.market)
                extra_details = market_temp.to_filter_details()
                for d in extra_details:
                    if d.get("rule_key") != "earnings_revision_momentum":
                        execution.ordered_outputs.append(FilterOutput(
                            filter_name=d.get("rule_key", d.get("filter_name", "market_rule")),
                            result=FilterResult.PASS if d.get("result") == "pass" else FilterResult.FAIL,
                            reason=d.get("reason", ""),
                            details=d,
                        ))
            except Exception:
                # 市场级规则计算失败不阻塞 Top20 筛选
                pass

        return StockFilterResult(
            stock=stock,
            passed=True,
            filter_outputs=execution.ordered_outputs,
        )

    def evaluate_stock(
        self,
        stock: StockInfo,
        filter_context: FilterContext,
        *,
        signal_analysis: Optional[Any] = None,
        signal_analysis_loader: Optional[Callable[[StockInfo], Any]] = None,
    ) -> StockFilterResult:
        cache_key = f"signal_analysis:{stock.code}"
        previous_analysis = filter_context.get_cache(cache_key)
        previous_loader = filter_context.get_cache("signal_analysis_loader")
        if signal_analysis is not None:
            filter_context.set_cache(cache_key, signal_analysis)
        if signal_analysis_loader is not None:
            filter_context.set_cache("signal_analysis_loader", signal_analysis_loader)
        execution = RuleExecutionContext(
            stock=stock,
            filter_context=filter_context,
            metadata_by_key=self.metadata_by_key,
            registry=self.registry,
        )
        try:
            passed = self.evaluator.evaluate(self.chain_config.expression, execution)
        finally:
            if previous_analysis is None:
                filter_context._cache.pop(cache_key, None)
            else:
                filter_context.set_cache(cache_key, previous_analysis)
            if previous_loader is None:
                filter_context._cache.pop("signal_analysis_loader", None)
            else:
                filter_context.set_cache("signal_analysis_loader", previous_loader)
        return StockFilterResult(
            stock=stock,
            passed=passed,
            filter_outputs=execution.ordered_outputs,
        )
