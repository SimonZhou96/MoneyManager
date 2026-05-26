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
    from .strategizers import (
        DailyPctChangeBandStrategizer,
        EMABreakoutStrategizer,
        RSIOverboughtStrategizer,
        RSIOversoldStrategizer,
        Strategizer,
        StrategizerOutput,
        TechnicalPatternStrategizer,
        TodayVolumeExceedsPrior3MaxStrategizer,
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
    from strategizers import (
        DailyPctChangeBandStrategizer,
        EMABreakoutStrategizer,
        RSIOverboughtStrategizer,
        RSIOversoldStrategizer,
        Strategizer,
        StrategizerOutput,
        TechnicalPatternStrategizer,
        TodayVolumeExceedsPrior3MaxStrategizer,
        ZuoYiStrategizer,
    )


RULE_TYPE_FILTER = "filter"
RULE_TYPE_STRATEGY = "strategy"


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

    def register_filter(self, implementation: str, factory: Callable[[dict], Filter]) -> "RuleRegistry":
        self._filter_factories[implementation] = factory
        return self

    def register_strategy(self, implementation: str, factory: Callable[[dict], Strategizer]) -> "RuleRegistry":
        self._strategy_factories[implementation] = factory
        return self

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
                    if key not in {"pattern_key", "direction", "pattern_label", "display_group"}
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
            ),
        )
        return registry


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

        try:
            implementation = self.registry.create(metadata)
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
            values = [context.execute(rule_key) for rule_key in rule_keys]
            return all(values)

        if "any_enabled" in expression:
            rule_keys = self._enabled_keys(expression["any_enabled"])
            if not rule_keys:
                return False
            values = [context.execute(rule_key) for rule_key in rule_keys]
            return any(values)

        return False

    def collect_rule_keys(self, expression: dict) -> Set[str]:
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

    def requires_macro_analysis(self) -> bool:
        return self.requires_signal_analysis() or self.requires_market_intel_macro_score()

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
