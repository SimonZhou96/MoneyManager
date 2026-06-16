"""Quant Lab — 策略回测集成测试"""
from __future__ import annotations

import pytest
import pandas as pd
import numpy as np
from datetime import date

from quant_lab.models import Bar, StrategyConfig, RiskConfig, ParamGrid, Signal
from quant_lab.strategies import create_strategy, list_strategies, STRATEGY_REGISTRY
from quant_lab.strategies.base import BaseStrategy
from quant_lab.backtest import BacktestRunner, BacktestRequest
from quant_lab.broker import SimulatedBroker
from quant_lab.metrics import calculate_metric_snapshot
from quant_lab.optimizer import GridSearchOptimizer


# ── Fixtures ──

def _make_bars(start: str, end: str, trend: float = 0.3, volatility: float = 0.02) -> list[Bar]:
    dates = pd.date_range(start, end, freq='B')
    close = [100.0]
    for _ in range(len(dates) - 1):
        close.append(close[-1] * (1 + trend / 252 + np.random.normal(0, volatility)))
    return [
        Bar(ts=d.date(), open=c * 0.999, high=c * 1.01, low=c * 0.99, close=c, volume=10000)
        for d, c in zip(dates, close)
    ]


class MockDataProvider:
    def bars_for(self, market, symbol, start, end):
        s = start.isoformat() if hasattr(start, 'isoformat') else str(start)
        e = end.isoformat() if hasattr(end, 'isoformat') else str(end)
        return _make_bars(s, e)


# ── 策略注册表测试 ──

def test_strategy_registry_has_three():
    assert len(STRATEGY_REGISTRY) >= 3
    for key in ('ma_cross', 'macd', 'rsi'):
        assert key in STRATEGY_REGISTRY, f'{key} should be registered'


def test_list_strategies():
    strategies = list_strategies()
    assert len(strategies) >= 3
    for s in strategies:
        assert 'type' in s
        assert 'name' in s
        assert 'params' in s


def test_create_strategy():
    s = create_strategy('ma_cross', {'fast': 5, 'slow': 20})
    assert s.config.strategy_type == 'ma_cross'
    assert s.config.params == {'fast': 5, 'slow': 20}
    with pytest.raises(ValueError, match='Unknown strategy'):
        create_strategy('nonexistent', {})


# ── 策略信号测试 ──

@pytest.mark.parametrize('stype,params', [
    ('ma_cross', {'fast': 3, 'slow': 10}),
    ('macd', {'fast': 12, 'slow': 26, 'signal': 9}),
    ('rsi', {'period': 14, 'oversold': 30, 'overbought': 70}),
])
def test_strategy_generates_signals(stype, params):
    np.random.seed(42)
    bars = _make_bars('2025-01-01', '2025-06-01', trend=0.5, volatility=0.04)
    df = pd.DataFrame([{
        'date': b.ts, 'open': b.open, 'high': b.high,
        'low': b.low, 'close': b.close, 'volume': b.volume,
    } for b in bars])
    strategy = create_strategy(stype, params)
    signals = strategy.generate_signals(df)
    assert len(signals) > 0, f'{stype} should generate at least 1 signal'
    assert all(s.direction in ('buy', 'sell') for s in signals)
    dates = [s.ts for s in signals]
    assert dates == sorted(dates), 'signals must be in chronological order'


def test_all_strategies_have_param_defs():
    for stype, cls in STRATEGY_REGISTRY.items():
        defs = cls.param_definitions()
        assert isinstance(defs, dict), f'{stype} param_definitions should be dict'
        for key, meta in defs.items():
            assert 'type' in meta
            assert 'default' in meta
            assert 'min' in meta
            assert 'max' in meta


# ── Broker 风控测试 ──

def test_broker_stop_loss():
    broker = SimulatedBroker(initial_cash=100000, risk_config=RiskConfig(stop_loss_pct=-0.05))
    buy_sig = Signal('sig1', date(2025, 1, 2), 'HK', '00700', 'buy', 'test')
    broker.apply_signal(buy_sig, Bar(date(2025, 1, 2), 100, 101, 99, 100, 1000), 10)
    assert broker.positions.get('00700') == 10
    result = broker.check_risk('00700', Bar(date(2025, 1, 3), 93, 94, 92, 93, 1000))
    assert result is not None, 'stop loss should trigger'
    assert broker.positions.get('00700', 0) == 0


def test_broker_take_profit():
    broker = SimulatedBroker(initial_cash=100000, risk_config=RiskConfig(take_profit_pct=0.10))
    buy_sig = Signal('sig1', date(2025, 1, 2), 'HK', '00700', 'buy', 'test')
    broker.apply_signal(buy_sig, Bar(date(2025, 1, 2), 100, 101, 99, 100, 1000), 10)
    result = broker.check_risk('00700', Bar(date(2025, 1, 15), 112, 113, 111, 112, 1000))
    assert result is not None, 'take profit should trigger'
    assert broker.positions.get('00700', 0) == 0


def test_broker_trailing_stop():
    broker = SimulatedBroker(initial_cash=100000, risk_config=RiskConfig(trailing_stop_pct=0.05))
    buy_sig = Signal('sig1', date(2025, 1, 2), 'HK', '00700', 'buy', 'test')
    broker.apply_signal(buy_sig, Bar(date(2025, 1, 2), 100, 101, 99, 100, 1000), 10)
    broker.check_risk('00700', Bar(date(2025, 1, 10), 120, 121, 119, 120, 1000))
    result = broker.check_risk('00700', Bar(date(2025, 1, 15), 113, 114, 112, 113, 1000))
    assert result is not None, 'trailing stop should trigger'
    assert broker.positions.get('00700', 0) == 0


def test_broker_no_risk_config_no_trigger():
    broker = SimulatedBroker(initial_cash=100000)
    buy_sig = Signal('sig1', date(2025, 1, 2), 'HK', '00700', 'buy', 'test')
    broker.apply_signal(buy_sig, Bar(date(2025, 1, 2), 100, 101, 99, 100, 1000), 10)
    result = broker.check_risk('00700', Bar(date(2025, 1, 3), 90, 91, 89, 90, 1000))
    assert result is None, 'no risk config → no forced exit'


# ── BacktestRunner 整合测试 ──

def test_backtest_runner_with_strategy():
    np.random.seed(42)
    strategy = create_strategy('ma_cross', {'fast': 3, 'slow': 10})
    runner = BacktestRunner(MockDataProvider(), strategy)
    result = runner.run(BacktestRequest(
        market='HK', symbols=['00700'],
        start=date(2025, 1, 1), end=date(2025, 6, 1),
        initial_cash=100000, quantity=10,
    ))
    assert len(result.signals) > 0
    assert len(result.equity_curve) > 0
    assert hasattr(result.metrics, 'sharpe')


def test_backtest_runner_with_risk():
    np.random.seed(42)
    strategy = create_strategy('macd', {'fast': 12, 'slow': 26, 'signal': 9})
    runner = BacktestRunner(MockDataProvider(), strategy)
    result = runner.run(BacktestRequest(
        market='US', symbols=['AAPL'],
        start=date(2025, 1, 1), end=date(2025, 6, 1),
        initial_cash=100000, quantity=10,
        risk_config=RiskConfig(stop_loss_pct=-0.10, take_profit_pct=0.30),
    ))
    assert len(result.equity_curve) > 0
    assert result.metrics.max_drawdown <= 0 or result.metrics.max_drawdown == 0


# ── Optimizer 测试 ──

def test_grid_search_optimizer():
    np.random.seed(42)
    optimizer = GridSearchOptimizer(MockDataProvider(), objective='sharpe')
    results = optimizer.optimize(
        market='HK', symbol='00700',
        start=date(2025, 1, 1), end=date(2025, 4, 1),
        param_grid=ParamGrid(
            strategy_type='ma_cross',
            param_space={'fast': [3, 5], 'slow': [10, 20]},
        ),
        initial_cash=100000,
    )
    assert len(results) == 4
    assert results[0].rank == 1
    assert results[0].objective_value >= results[-1].objective_value


def test_optimizer_all_results_sorted():
    np.random.seed(42)
    optimizer = GridSearchOptimizer(MockDataProvider(), objective='total_return')
    results = optimizer.optimize(
        market='A', symbol='000001',
        start=date(2025, 1, 1), end=date(2025, 3, 1),
        param_grid=ParamGrid(
            strategy_type='rsi',
            param_space={'period': [10, 14], 'oversold': [25, 30], 'overbought': [70, 75]},
        ),
    )
    for i in range(len(results) - 1):
        assert results[i].objective_value >= results[i + 1].objective_value


# ── Metrics 测试 ──

def test_extended_metrics_present():
    np.random.seed(42)
    strategy = create_strategy('ma_cross', {'fast': 3, 'slow': 10})
    runner = BacktestRunner(MockDataProvider(), strategy)
    result = runner.run(BacktestRequest(
        market='HK', symbols=['00700'],
        start=date(2025, 1, 1), end=date(2025, 6, 1),
        initial_cash=100000, quantity=10,
    ))
    m = result.metrics
    for attr in ('sharpe', 'sortino', 'calmar', 'cagr', 'annual_volatility'):
        assert hasattr(m, attr), f'{attr} field should exist'
