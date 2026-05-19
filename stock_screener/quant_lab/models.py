from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Bar:
    ts: date
    open: float
    high: float
    low: float
    close: float
    volume: float = 0


@dataclass(frozen=True)
class Signal:
    signal_id: str
    ts: date
    market: str
    symbol: str
    direction: str
    reason: str
    strength: float = 1.0
    rule_chain_key: str = ""
    rule_chain_snapshot: Dict[str, Any] = field(default_factory=dict)
    passed_rules: List[str] = field(default_factory=list)
    failed_rules: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class Order:
    order_id: str
    signal_id: str
    ts: date
    symbol: str
    side: str
    quantity: int
    order_type: str = "market"
    limit_price: Optional[float] = None
    status: str = "created"
    rejected_reason: str = ""


@dataclass(frozen=True)
class Trade:
    order_id: str
    trade_id: str
    symbol: str
    side: str
    quantity: int
    price: float
    fee: float
    slippage: float
    ts: Optional[date] = None


@dataclass(frozen=True)
class PositionSnapshot:
    ts: date
    symbol: str
    quantity: int
    average_cost: float
    market_value: float
    unrealized_pnl: float


@dataclass(frozen=True)
class EquityPoint:
    ts: date
    equity: float
    cash: float
    drawdown: float
    benchmark_value: Optional[float] = None


@dataclass(frozen=True)
class MetricSnapshot:
    total_return: float = 0
    annualized_return: float = 0
    benchmark_return: float = 0
    excess_return: float = 0
    max_drawdown: float = 0
    drawdown_duration: int = 0
    volatility: float = 0
    return_drawdown_ratio: float = 0
    trade_count: int = 0
    win_rate: float = 0
    average_win: float = 0
    average_loss: float = 0
    win_loss_ratio: float = 0
    profit_factor: float = 0
    expected_value: float = 0
    max_consecutive_losses: int = 0
    average_holding_period: float = 0
    capital_utilization: float = 0
    max_position_weight: float = 0
    option_metrics: Dict[str, Any] = field(default_factory=dict)
