"""Quant Lab 经典策略库"""

from .base import BaseStrategy, STRATEGY_REGISTRY, _register
from .ma_cross import MACrossStrategy  # noqa: F401 — triggers @_register
from .macd import MACDStrategy        # noqa: F401
from .rsi import RSIStrategy          # noqa: F401


def list_strategies() -> list[dict]:
    """返回所有已注册策略的元信息（供 API 使用）"""
    return [
        {
            "type": cls.strategy_type(),
            "name": cls.name(),
            "params": cls.param_definitions(),
        }
        for cls in STRATEGY_REGISTRY.values()
    ]


def create_strategy(strategy_type: str, params: dict, **kwargs) -> BaseStrategy:
    """工厂方法：根据 strategy_type 创建策略实例"""
    from ..models import StrategyConfig
    cls = STRATEGY_REGISTRY.get(strategy_type)
    if cls is None:
        raise ValueError(f"Unknown strategy type: {strategy_type}. Available: {list(STRATEGY_REGISTRY.keys())}")
    config = StrategyConfig(strategy_type=strategy_type, params=params, **kwargs)
    return cls(config)
