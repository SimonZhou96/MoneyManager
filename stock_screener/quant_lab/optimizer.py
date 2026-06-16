from __future__ import annotations

import itertools
from datetime import date
from typing import Optional

from .backtest import BacktestRequest, BacktestRunner
from .models import OptimizationResult, ParamGrid, RiskConfig
from .strategies import create_strategy


class GridSearchOptimizer:
    """网格搜索参数优化器"""

    def __init__(self, data_provider, objective: str = "sharpe"):
        self.data_provider = data_provider
        self.objective = objective

    def optimize(
        self,
        market: str,
        symbol: str,
        start: date,
        end: date,
        param_grid: ParamGrid,
        initial_cash: float = 100_000,
        risk_config: Optional[RiskConfig] = None,
        quantity: int = 10,
    ) -> list[OptimizationResult]:
        """遍历所有参数组合，返回按目标排序的结果列表"""
        keys = list(param_grid.param_space.keys())
        values = [param_grid.param_space[k] for k in keys]
        combinations = list(itertools.product(*values))

        results: list[OptimizationResult] = []

        for combo in combinations:
            params = dict(zip(keys, combo))
            try:
                strategy = create_strategy(param_grid.strategy_type, params)
                runner = BacktestRunner(self.data_provider, strategy)
                result = runner.run(BacktestRequest(
                    market=market,
                    symbols=[symbol],
                    start=start,
                    end=end,
                    initial_cash=initial_cash,
                    quantity=quantity,
                    risk_config=risk_config,
                ))
                obj_value = self._extract_objective(result.metrics)
                results.append(OptimizationResult(
                    params=params,
                    metrics=result.metrics,
                    equity_curve=result.equity_curve,
                    rank=0,
                    objective_value=obj_value,
                ))
            except Exception:
                continue

        results.sort(key=lambda r: r.objective_value, reverse=True)
        for i, r in enumerate(results):
            object.__setattr__(r, "rank", i + 1)

        return results

    def _extract_objective(self, metrics) -> float:
        attr_map = {
            "sharpe": "sharpe",
            "total_return": "total_return",
            "calmar": "calmar",
            "win_rate": "win_rate",
            "profit_factor": "profit_factor",
        }
        attr = attr_map.get(self.objective, "sharpe")
        return float(getattr(metrics, attr, 0) or 0)
