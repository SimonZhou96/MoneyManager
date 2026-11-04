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
    # 新增字段
    sharpe: float = 0
    sortino: float = 0
    calmar: float = 0
    cagr: float = 0
    annual_volatility: float = 0
    option_metrics: Dict[str, Any] = field(default_factory=dict)


def bar_to_dict(bar: Bar) -> Dict[str, Any]:
    return {
        "date": bar.ts.isoformat(),
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "volume": float(bar.volume),
    }


def signal_to_dict(signal: Signal, price: Optional[float] = None) -> Dict[str, Any]:
    return {
        "signal_id": signal.signal_id,
        "date": signal.ts.isoformat(),
        "market": signal.market,
        "symbol": signal.symbol,
        "direction": signal.direction,
        "reason": signal.reason,
        "strength": float(signal.strength),
        "rule_chain_key": signal.rule_chain_key,
        "price": price,
    }


def trade_to_dict(trade: Trade) -> Dict[str, Any]:
    return {
        "trade_id": trade.trade_id,
        "order_id": trade.order_id,
        "date": trade.ts.isoformat() if trade.ts else None,
        "symbol": trade.symbol,
        "side": trade.side,
        "quantity": int(trade.quantity),
        "price": float(trade.price),
        "fee": float(trade.fee),
        "slippage": float(trade.slippage),
    }


# ---------------------------------------------------------------------------
# Strategy layer models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StrategyConfig:
    """策略配置（JSON-serializable，对前端暴露）"""
    strategy_type: str          # "ma_cross" | "macd" | "rsi" | "bollinger" | "momentum" | "turtle"
    params: dict                # 策略参数，如 {"fast": 5, "slow": 20}
    entry_side: str = "long"    # "long" | "short" | "both"


@dataclass(frozen=True)
class ParamGrid:
    """参数网格定义"""
    strategy_type: str
    param_space: dict           # {"fast": [5,10,20], "slow": [20,30,60]}
    objective: str = "sharpe"   # "sharpe" | "total_return" | "calmar" | "win_rate"


@dataclass(frozen=True)
class RiskConfig:
    """风控配置（可选）"""
    stop_loss_pct: Optional[float] = None        # 固定止损比例，如 -0.08
    take_profit_pct: Optional[float] = None      # 固定止盈比例，如 +0.20
    trailing_stop_pct: Optional[float] = None    # 移动止损，回撤超过 X% 出场


@dataclass(frozen=True)
class OptimizationResult:
    """一组参数的回测结果"""
    params: dict                        # {"fast": 5, "slow": 20}
    metrics: "MetricSnapshot"           # 完整绩效指标
    equity_curve: list                  # list[EquityPoint]
    rank: int                           # 排名（按 objective）
    objective_value: float              # 目标函数值
