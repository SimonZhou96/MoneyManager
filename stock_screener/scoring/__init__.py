#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
评分模块：将规则引擎输出的 filter_details 聚合为 entry_score（入场信号分）
和 holding_score（持有价值分）。与 RuleEngine 解耦，只消费其输出。
"""

from .models import (
    EntryScoreBreakdown,
    HoldingScoreBreakdown,
    MarketTemperature,
    CreditRiskResult,
    MarketBreadthResult,
    LiquidityNowcastResult,
    EarningsRevisionResult,
    PolicyEventResult,
    CommodityShockResult,
)
from .constants import (
    ENTRY_WEIGHTS,
    HOLDING_WEIGHTS,
    DECISION_MAP_ENTRY,
    DECISION_MAP_HOLDING,
    RULE_TO_MODULE_MAP,
)
# from .entry_scorer import EntryScorer      # uncomment in Task 10
# from .holding_scorer import HoldingScorer  # uncomment in Task 11
# from .market_cache import MarketCache      # uncomment in Task 12

__all__ = [
    "EntryScoreBreakdown",
    "HoldingScoreBreakdown",
    "MarketTemperature",
    "CreditRiskResult",
    "MarketBreadthResult",
    "LiquidityNowcastResult",
    "EarningsRevisionResult",
    "PolicyEventResult",
    "CommodityShockResult",
    "ENTRY_WEIGHTS",
    "HOLDING_WEIGHTS",
    "DECISION_MAP_ENTRY",
    "DECISION_MAP_HOLDING",
    "RULE_TO_MODULE_MAP",
    # "EntryScorer",      # uncomment in Task 10
    # "HoldingScorer",    # uncomment in Task 11
    # "MarketCache",      # uncomment in Task 12
]
