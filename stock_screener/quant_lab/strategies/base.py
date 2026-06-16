from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from ..models import Signal, StrategyConfig


class BaseStrategy(ABC):
    """经典量化策略抽象基类

    约定：一套参数 → 一次 generate_signals() → 完整信号序列（纯函数，无状态）
    策略不感知资金/仓位——只输出方向信号，引擎统一执行
    """

    def __init__(self, config: StrategyConfig):
        self.config = config

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> list[Signal]:
        """
        Args:
            df: 标准化 K 线 DataFrame，至少包含 date, open, high, low, close, volume

        Returns:
            买卖信号列表，按日期排序
        """
        ...

    @staticmethod
    @abstractmethod
    def strategy_type() -> str:
        """策略标识，如 'ma_cross'"""
        ...

    @staticmethod
    @abstractmethod
    def name() -> str:
        """策略中文名，如 '双均线交叉'"""
        ...

    @staticmethod
    @abstractmethod
    def param_definitions() -> dict:
        """参数定义，供前端动态渲染表单
        {
            "fast": {"type": "int", "default": 5, "min": 2, "max": 120},
            "slow": {"type": "int", "default": 20, "min": 5, "max": 250},
        }
        """
        ...
