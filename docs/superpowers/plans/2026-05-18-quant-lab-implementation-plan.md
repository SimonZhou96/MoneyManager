# Quant Lab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Quant Lab that reuses existing MoneyManager rule chains as quantitative strategies, backtests them through simulated trades, reports win-rate/risk/return metrics, and supports paper trading without real Futu orders.

**Architecture:** Add a focused `stock_screener/quant_lab/` package with normalized models, metrics, broker, rule-chain adapter, backtest runner, persistence service, paper-trading service, and limited Option Lab replay. Add `web/quant.py` APIs and a dedicated frontend Quant Lab page extracted into focused frontend files instead of expanding the existing monolithic `main.tsx` further.

**Tech Stack:** Python 3, unittest, pandas, FastAPI, Pydantic, MySQL JSON columns, React, TypeScript, Vite.

---

## File Structure

- Create `stock_screener/quant_lab/__init__.py`: package marker and public exports.
- Create `stock_screener/quant_lab/models.py`: dataclasses/enums for bars, signals, orders, trades, positions, equity points, metrics, and requests.
- Create `stock_screener/quant_lab/metrics.py`: deterministic portfolio/trade metric calculations.
- Create `stock_screener/quant_lab/broker.py`: simulated broker that converts signals into orders/trades and applies fees, slippage, and risk gates.
- Create `stock_screener/quant_lab/rule_chain_adapter.py`: adapts existing `RuleEngine` and rule-chain configs into historical buy/sell/hold signals.
- Create `stock_screener/quant_lab/backtest.py`: event-driven loop that loads bars, evaluates strategy, applies broker, and produces results.
- Create `stock_screener/quant_lab/service.py`: orchestration layer used by API and future CLI/worker callers.
- Create `stock_screener/quant_lab/option_adapter.py`: limited Option Lab candidate replay adapter.
- Create `stock_screener/quant_lab/paper.py`: paper account/order/position service.
- Create `stock_screener/web/quant.py`: FastAPI router for backtests, results, and paper trading.
- Modify `stock_screener/web/main.py`: include the Quant Lab router.
- Modify `stock_screener/db.py`: add schema initialization and repository methods for quant tables.
- Create `stock_screener/sql/014_quant_lab.sql`: MySQL schema for Quant Lab.
- Create tests:
  - `stock_screener/tests/test_quant_metrics.py`
  - `stock_screener/tests/test_quant_broker.py`
  - `stock_screener/tests/test_quant_rule_chain_adapter.py`
  - `stock_screener/tests/test_quant_backtest.py`
  - `stock_screener/tests/test_quant_api.py`
  - `stock_screener/tests/test_quant_option_adapter.py`
- Create frontend files:
  - `stock_screener/web_frontend/src/api.ts`
  - `stock_screener/web_frontend/src/features/quant/types.ts`
  - `stock_screener/web_frontend/src/features/quant/QuantLab.tsx`
  - `stock_screener/web_frontend/src/features/quant/quantFormat.ts`
- Modify `stock_screener/web_frontend/src/main.tsx`: route/render Quant Lab through the extracted component.
- Modify `stock_screener/web_frontend/src/styles.css`: add focused Quant Lab classes.

## Implementation Rules

- Preserve existing uncommitted user changes. Before every task, run `git -C MoneyManager status --short` and only stage files named in that task.
- Do not stage `.superpowers/`.
- Do not call Futu real order-placement APIs.
- Do not invoke search providers or LLM providers in default backtests.
- Treat entry-only rule chains as signal analysis unless an exit policy is configured.
- Store a rule-chain snapshot with every backtest run so future edits do not rewrite historical results.

---

### Task 1: Quant Models And Metrics

**Files:**
- Create: `stock_screener/quant_lab/__init__.py`
- Create: `stock_screener/quant_lab/models.py`
- Create: `stock_screener/quant_lab/metrics.py`
- Test: `stock_screener/tests/test_quant_metrics.py`

- [ ] **Step 1: Write failing metric tests**

Create `stock_screener/tests/test_quant_metrics.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import date

from quant_lab.metrics import calculate_metric_snapshot
from quant_lab.models import EquityPoint, Trade


class QuantMetricsTest(unittest.TestCase):
    def test_calculates_return_drawdown_win_rate_and_expected_value(self):
        equity = [
            EquityPoint(ts=date(2026, 1, 1), equity=10000, cash=10000, drawdown=0, benchmark_value=10000),
            EquityPoint(ts=date(2026, 1, 2), equity=11000, cash=8000, drawdown=0, benchmark_value=10100),
            EquityPoint(ts=date(2026, 1, 3), equity=10500, cash=10500, drawdown=-0.0454545, benchmark_value=10050),
        ]
        trades = [
            Trade(order_id="o1", trade_id="t1", symbol="US.AAPL", side="buy", quantity=10, price=100, fee=1, slippage=0),
            Trade(order_id="o2", trade_id="t2", symbol="US.AAPL", side="sell", quantity=10, price=110, fee=1, slippage=0),
            Trade(order_id="o3", trade_id="t3", symbol="US.MSFT", side="buy", quantity=5, price=200, fee=1, slippage=0),
            Trade(order_id="o4", trade_id="t4", symbol="US.MSFT", side="sell", quantity=5, price=190, fee=1, slippage=0),
        ]

        metrics = calculate_metric_snapshot(equity, trades)

        self.assertAlmostEqual(metrics.total_return, 0.05, places=4)
        self.assertAlmostEqual(metrics.max_drawdown, -0.0454545, places=4)
        self.assertEqual(metrics.trade_count, 2)
        self.assertAlmostEqual(metrics.win_rate, 0.5, places=4)
        self.assertAlmostEqual(metrics.average_win, 98.0, places=4)
        self.assertAlmostEqual(metrics.average_loss, -52.0, places=4)
        self.assertAlmostEqual(metrics.expected_value, 23.0, places=4)

    def test_empty_inputs_return_zero_metrics(self):
        metrics = calculate_metric_snapshot([], [])
        self.assertEqual(metrics.total_return, 0)
        self.assertEqual(metrics.trade_count, 0)
        self.assertEqual(metrics.win_rate, 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_quant_metrics -v
```

Expected: fail with `ModuleNotFoundError: No module named 'quant_lab'`.

- [ ] **Step 3: Add model dataclasses**

Create `stock_screener/quant_lab/__init__.py`:

```python
"""Quant Lab backtesting and paper-trading package."""
```

Create `stock_screener/quant_lab/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
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
    direction: str  # buy / sell / hold
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
```

- [ ] **Step 4: Implement metric calculations**

Create `stock_screener/quant_lab/metrics.py`:

```python
from __future__ import annotations

from typing import Iterable, List

from .models import EquityPoint, MetricSnapshot, Trade


def _round(value: float) -> float:
    return round(float(value or 0), 6)


def _pair_trade_pnls(trades: Iterable[Trade]) -> List[float]:
    open_positions = {}
    pnls: List[float] = []
    for trade in trades:
        key = trade.symbol
        if trade.side == "buy":
            open_positions[key] = {
                "quantity": int(trade.quantity),
                "price": float(trade.price),
                "fee": float(trade.fee or 0),
            }
            continue
        if trade.side == "sell" and key in open_positions:
            opened = open_positions.pop(key)
            qty = min(int(trade.quantity), int(opened["quantity"]))
            gross = (float(trade.price) - float(opened["price"])) * qty
            pnls.append(gross - float(opened["fee"]) - float(trade.fee or 0))
    return pnls


def _max_consecutive_losses(pnls: List[float]) -> int:
    best = 0
    current = 0
    for pnl in pnls:
        if pnl < 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def calculate_metric_snapshot(equity: List[EquityPoint], trades: List[Trade]) -> MetricSnapshot:
    if not equity:
        return MetricSnapshot()

    start = float(equity[0].equity or 0)
    end = float(equity[-1].equity or 0)
    total_return = (end - start) / start if start > 0 else 0
    max_drawdown = min((point.drawdown for point in equity), default=0)
    benchmark_return = 0
    if equity[0].benchmark_value and equity[-1].benchmark_value:
        benchmark_return = (float(equity[-1].benchmark_value) - float(equity[0].benchmark_value)) / float(equity[0].benchmark_value)

    pnls = _pair_trade_pnls(trades)
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    trade_count = len(pnls)
    win_rate = len(wins) / trade_count if trade_count else 0
    average_win = sum(wins) / len(wins) if wins else 0
    average_loss = sum(losses) / len(losses) if losses else 0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_win / gross_loss if gross_loss else 0
    expected_value = sum(pnls) / trade_count if trade_count else 0

    return MetricSnapshot(
        total_return=_round(total_return),
        benchmark_return=_round(benchmark_return),
        excess_return=_round(total_return - benchmark_return),
        max_drawdown=_round(max_drawdown),
        trade_count=trade_count,
        win_rate=_round(win_rate),
        average_win=_round(average_win),
        average_loss=_round(average_loss),
        win_loss_ratio=_round(abs(average_win / average_loss)) if average_loss else 0,
        profit_factor=_round(profit_factor),
        expected_value=_round(expected_value),
        max_consecutive_losses=_max_consecutive_losses(pnls),
    )
```

- [ ] **Step 5: Run tests to verify pass**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_quant_metrics -v
```

Expected: `Ran 2 tests` and `OK`.

- [ ] **Step 6: Commit**

Run:

```bash
git -C /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager add stock_screener/quant_lab/__init__.py stock_screener/quant_lab/models.py stock_screener/quant_lab/metrics.py stock_screener/tests/test_quant_metrics.py
git -C /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager commit -m "Add quant lab models and metrics"
```

Expected: commit includes only the four files listed above.

---

### Task 2: Simulated Broker

**Files:**
- Create: `stock_screener/quant_lab/broker.py`
- Test: `stock_screener/tests/test_quant_broker.py`

- [ ] **Step 1: Write failing broker tests**

Create `stock_screener/tests/test_quant_broker.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import date

from quant_lab.broker import SimulatedBroker
from quant_lab.models import Bar, Signal


class SimulatedBrokerTest(unittest.TestCase):
    def test_buy_signal_creates_filled_trade_with_fee_and_slippage(self):
        broker = SimulatedBroker(initial_cash=10000, commission_rate=0.001, slippage_rate=0.002, max_position_weight=1.0)
        signal = Signal(signal_id="s1", ts=date(2026, 1, 2), market="US", symbol="US.AAPL", direction="buy", reason="rule pass")
        bar = Bar(ts=date(2026, 1, 2), open=100, high=101, low=99, close=100, volume=100000)

        order, trade = broker.apply_signal(signal, bar, quantity=10)

        self.assertEqual(order.status, "filled")
        self.assertIsNotNone(trade)
        self.assertAlmostEqual(trade.price, 100.2, places=4)
        self.assertAlmostEqual(trade.fee, 1.002, places=4)
        self.assertLess(broker.cash, 10000)

    def test_rejects_order_when_max_position_weight_is_exceeded(self):
        broker = SimulatedBroker(initial_cash=1000, commission_rate=0.001, slippage_rate=0, max_position_weight=0.1)
        signal = Signal(signal_id="s1", ts=date(2026, 1, 2), market="US", symbol="US.AAPL", direction="buy", reason="rule pass")
        bar = Bar(ts=date(2026, 1, 2), open=100, high=101, low=99, close=100, volume=100000)

        order, trade = broker.apply_signal(signal, bar, quantity=10)

        self.assertEqual(order.status, "rejected")
        self.assertEqual(order.rejected_reason, "max_position_weight_exceeded")
        self.assertIsNone(trade)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_quant_broker -v
```

Expected: fail with `ModuleNotFoundError: No module named 'quant_lab.broker'`.

- [ ] **Step 3: Implement broker**

Create `stock_screener/quant_lab/broker.py`:

```python
from __future__ import annotations

import uuid
from typing import Optional, Tuple

from .models import Bar, Order, Signal, Trade


class SimulatedBroker:
    def __init__(
        self,
        initial_cash: float,
        commission_rate: float = 0.001,
        slippage_rate: float = 0.001,
        max_position_weight: float = 1.0,
    ):
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.commission_rate = float(commission_rate)
        self.slippage_rate = float(slippage_rate)
        self.max_position_weight = float(max_position_weight)
        self.positions = {}

    def apply_signal(self, signal: Signal, bar: Bar, quantity: int = 1) -> Tuple[Order, Optional[Trade]]:
        side = "buy" if signal.direction == "buy" else "sell" if signal.direction == "sell" else "hold"
        order = Order(
            order_id=str(uuid.uuid4()),
            signal_id=signal.signal_id,
            ts=signal.ts,
            symbol=signal.symbol,
            side=side,
            quantity=int(quantity),
        )
        if side == "hold":
            return Order(**{**order.__dict__, "status": "ignored", "rejected_reason": "hold_signal"}), None

        fill_price = self._fill_price(side, bar)
        notional = fill_price * int(quantity)
        fee = notional * self.commission_rate
        if side == "buy" and not self._can_buy(notional + fee):
            return Order(**{**order.__dict__, "status": "rejected", "rejected_reason": "insufficient_cash"}), None
        if side == "buy" and not self._within_position_limit(notional):
            return Order(**{**order.__dict__, "status": "rejected", "rejected_reason": "max_position_weight_exceeded"}), None

        trade = Trade(
            order_id=order.order_id,
            trade_id=str(uuid.uuid4()),
            symbol=signal.symbol,
            side=side,
            quantity=int(quantity),
            price=round(fill_price, 6),
            fee=round(fee, 6),
            slippage=round(abs(fill_price - float(bar.close)), 6),
            ts=signal.ts,
        )
        self._apply_trade(trade)
        return Order(**{**order.__dict__, "status": "filled"}), trade

    def _fill_price(self, side: str, bar: Bar) -> float:
        close = float(bar.close)
        if side == "buy":
            return close * (1 + self.slippage_rate)
        return close * (1 - self.slippage_rate)

    def _can_buy(self, required_cash: float) -> bool:
        return self.cash >= required_cash

    def _within_position_limit(self, new_notional: float) -> bool:
        return new_notional <= self.initial_cash * self.max_position_weight

    def _apply_trade(self, trade: Trade) -> None:
        notional = trade.price * trade.quantity
        if trade.side == "buy":
            self.cash -= notional + trade.fee
            self.positions[trade.symbol] = self.positions.get(trade.symbol, 0) + trade.quantity
        elif trade.side == "sell":
            self.cash += notional - trade.fee
            self.positions[trade.symbol] = self.positions.get(trade.symbol, 0) - trade.quantity
```

- [ ] **Step 4: Run broker tests**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_quant_broker -v
```

Expected: `Ran 2 tests` and `OK`.

- [ ] **Step 5: Commit**

Run:

```bash
git -C /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager add stock_screener/quant_lab/broker.py stock_screener/tests/test_quant_broker.py
git -C /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager commit -m "Add simulated quant broker"
```

Expected: commit includes only broker and broker test files.

---

### Task 3: Rule Chain Adapter

**Files:**
- Create: `stock_screener/quant_lab/rule_chain_adapter.py`
- Test: `stock_screener/tests/test_quant_rule_chain_adapter.py`

- [ ] **Step 1: Write failing rule-chain adapter tests**

Create `stock_screener/tests/test_quant_rule_chain_adapter.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import date

from filters import FilterContext, StockInfo
from quant_lab.rule_chain_adapter import RuleChainStrategyAdapter
from rule_engine import RuleChainConfig, RuleMetadata


class FakeRuleEngine:
    def __init__(self, passed_dates):
        self.passed_dates = set(passed_dates)

    def evaluate_stock(self, stock, context):
        class Result:
            passed = context.check_date in self_dates
            filter_outputs = []
        self_dates = self.passed_dates
        return Result()


class RuleChainStrategyAdapterTest(unittest.TestCase):
    def test_entry_signal_is_emitted_when_rule_chain_passes(self):
        chain = RuleChainConfig(market="US", timeframe="1d", chain_key="zuoyi", chain_name="左一", expression={"ref": "zuoyi_signal"})
        adapter = RuleChainStrategyAdapter(chain=chain, rule_engine=FakeRuleEngine({date(2026, 1, 2)}), exit_policy={"type": "fixed_holding_days", "days": 2})

        signals = adapter.evaluate(
            stock=StockInfo(market="US", code="US.AAPL", name="Apple"),
            dates=[date(2026, 1, 1), date(2026, 1, 2)],
        )

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].direction, "buy")
        self.assertEqual(signals[0].rule_chain_key, "zuoyi")
        self.assertIn("rule chain passed", signals[0].reason)

    def test_entry_only_chain_without_exit_policy_is_signal_analysis(self):
        chain = RuleChainConfig(market="US", timeframe="1d", chain_key="entry_only", chain_name="entry", expression={"ref": "zuoyi_signal"})
        adapter = RuleChainStrategyAdapter(chain=chain, rule_engine=FakeRuleEngine({date(2026, 1, 2)}), exit_policy=None)

        self.assertEqual(adapter.mode, "signal_analysis")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_quant_rule_chain_adapter -v
```

Expected: fail with `ModuleNotFoundError: No module named 'quant_lab.rule_chain_adapter'`.

- [ ] **Step 3: Implement rule-chain adapter**

Create `stock_screener/quant_lab/rule_chain_adapter.py`:

```python
from __future__ import annotations

import uuid
from datetime import date
from typing import Iterable, Optional

from filters import FilterContext, StockInfo
from rule_engine import RuleChainConfig

from .models import Signal


class RuleChainStrategyAdapter:
    def __init__(self, chain: RuleChainConfig, rule_engine, exit_policy: Optional[dict] = None):
        self.chain = chain
        self.rule_engine = rule_engine
        self.exit_policy = exit_policy or {}
        self.mode = "trade_backtest" if self.exit_policy else "signal_analysis"

    def evaluate(self, stock: StockInfo, dates: Iterable[date]) -> list[Signal]:
        signals: list[Signal] = []
        for item in dates:
            context = FilterContext(check_date=item, market=stock.market)
            result = self.rule_engine.evaluate_stock(stock, context)
            if not getattr(result, "passed", False):
                continue
            signals.append(
                Signal(
                    signal_id=str(uuid.uuid4()),
                    ts=item,
                    market=stock.market,
                    symbol=stock.code,
                    direction="buy",
                    reason=f"rule chain passed: {self.chain.chain_name or self.chain.chain_key}",
                    rule_chain_key=self.chain.chain_key,
                    rule_chain_snapshot={
                        "market": self.chain.market,
                        "timeframe": self.chain.timeframe,
                        "chain_key": self.chain.chain_key,
                        "chain_name": self.chain.chain_name,
                        "expression": self.chain.expression,
                        "exit_policy": self.exit_policy,
                    },
                    passed_rules=self._passed_rules(result),
                    failed_rules=[],
                )
            )
        return signals

    def _passed_rules(self, result) -> list[str]:
        outputs = getattr(result, "filter_outputs", []) or []
        values = []
        for output in outputs:
            name = getattr(output, "filter_name", "") or getattr(output, "name", "")
            if name:
                values.append(str(name))
        return values
```

- [ ] **Step 4: Run adapter tests**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_quant_rule_chain_adapter -v
```

Expected: `Ran 2 tests` and `OK`.

- [ ] **Step 5: Commit**

Run:

```bash
git -C /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager add stock_screener/quant_lab/rule_chain_adapter.py stock_screener/tests/test_quant_rule_chain_adapter.py
git -C /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager commit -m "Add rule chain strategy adapter"
```

Expected: commit includes only adapter and test files.

---

### Task 4: Backtest Runner

**Files:**
- Create: `stock_screener/quant_lab/backtest.py`
- Test: `stock_screener/tests/test_quant_backtest.py`

- [ ] **Step 1: Write failing backtest integration test**

Create `stock_screener/tests/test_quant_backtest.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import date

from quant_lab.backtest import BacktestRunner, BacktestRequest
from quant_lab.models import Bar, Signal


class StaticStrategy:
    mode = "trade_backtest"

    def evaluate(self, stock, dates):
        return [
            Signal(signal_id="s1", ts=date(2026, 1, 1), market="US", symbol="US.AAPL", direction="buy", reason="entry"),
            Signal(signal_id="s2", ts=date(2026, 1, 3), market="US", symbol="US.AAPL", direction="sell", reason="exit"),
        ]


class StaticDataProvider:
    def bars_for(self, market, symbol, start, end):
        return [
            Bar(ts=date(2026, 1, 1), open=100, high=101, low=99, close=100, volume=1000),
            Bar(ts=date(2026, 1, 2), open=105, high=106, low=104, close=105, volume=1000),
            Bar(ts=date(2026, 1, 3), open=110, high=111, low=109, close=110, volume=1000),
        ]


class BacktestRunnerTest(unittest.TestCase):
    def test_runner_generates_trades_equity_and_metrics(self):
        request = BacktestRequest(
            market="US",
            symbols=["US.AAPL"],
            start=date(2026, 1, 1),
            end=date(2026, 1, 3),
            initial_cash=10000,
            quantity=10,
        )
        runner = BacktestRunner(data_provider=StaticDataProvider(), strategy=StaticStrategy())

        result = runner.run(request)

        self.assertEqual(len(result.signals), 2)
        self.assertEqual(len(result.trades), 2)
        self.assertGreater(result.metrics.total_return, 0)
        self.assertEqual(result.metrics.trade_count, 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_quant_backtest -v
```

Expected: fail with `ModuleNotFoundError: No module named 'quant_lab.backtest'`.

- [ ] **Step 3: Implement backtest runner request/result and loop**

Create `stock_screener/quant_lab/backtest.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List

from filters import StockInfo

from .broker import SimulatedBroker
from .metrics import calculate_metric_snapshot
from .models import Bar, EquityPoint, MetricSnapshot, Order, Signal, Trade


@dataclass(frozen=True)
class BacktestRequest:
    market: str
    symbols: List[str]
    start: date
    end: date
    initial_cash: float
    quantity: int = 1
    commission_rate: float = 0.001
    slippage_rate: float = 0.001
    max_position_weight: float = 1.0


@dataclass(frozen=True)
class BacktestResult:
    signals: List[Signal] = field(default_factory=list)
    orders: List[Order] = field(default_factory=list)
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[EquityPoint] = field(default_factory=list)
    metrics: MetricSnapshot = field(default_factory=MetricSnapshot)
    warnings: List[str] = field(default_factory=list)


class BacktestRunner:
    def __init__(self, data_provider, strategy):
        self.data_provider = data_provider
        self.strategy = strategy

    def run(self, request: BacktestRequest) -> BacktestResult:
        broker = SimulatedBroker(
            initial_cash=request.initial_cash,
            commission_rate=request.commission_rate,
            slippage_rate=request.slippage_rate,
            max_position_weight=request.max_position_weight,
        )
        signals: list[Signal] = []
        orders: list[Order] = []
        trades: list[Trade] = []
        equity: list[EquityPoint] = []
        warnings: list[str] = []

        for symbol in request.symbols:
            bars = self.data_provider.bars_for(request.market, symbol, request.start, request.end)
            if not bars:
                warnings.append(f"missing_bars:{symbol}")
                continue
            bar_by_date = {bar.ts: bar for bar in bars}
            dates = [bar.ts for bar in bars]
            stock = StockInfo(market=request.market, code=symbol, name=symbol)
            symbol_signals = self.strategy.evaluate(stock, dates)
            signals.extend(symbol_signals)
            for signal in symbol_signals:
                bar = bar_by_date.get(signal.ts)
                if not bar:
                    warnings.append(f"missing_signal_bar:{symbol}:{signal.ts.isoformat()}")
                    continue
                order, trade = broker.apply_signal(signal, bar, quantity=request.quantity)
                orders.append(order)
                if trade:
                    trades.append(trade)
            for bar in bars:
                equity.append(EquityPoint(ts=bar.ts, equity=broker.cash + _market_value(broker, symbol, bar), cash=broker.cash, drawdown=0))

        equity = _with_drawdowns(equity)
        metrics = calculate_metric_snapshot(equity, trades)
        return BacktestResult(signals=signals, orders=orders, trades=trades, equity_curve=equity, metrics=metrics, warnings=warnings)


def _market_value(broker: SimulatedBroker, symbol: str, bar: Bar) -> float:
    return broker.positions.get(symbol, 0) * float(bar.close)


def _with_drawdowns(points: list[EquityPoint]) -> list[EquityPoint]:
    peak = 0.0
    result = []
    for point in points:
        peak = max(peak, float(point.equity))
        drawdown = (float(point.equity) - peak) / peak if peak else 0
        result.append(EquityPoint(ts=point.ts, equity=point.equity, cash=point.cash, drawdown=drawdown, benchmark_value=point.benchmark_value))
    return result
```

- [ ] **Step 4: Run backtest tests**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_quant_backtest -v
```

Expected: `Ran 1 test` and `OK`.

- [ ] **Step 5: Run combined quant unit tests**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_quant_metrics tests.test_quant_broker tests.test_quant_rule_chain_adapter tests.test_quant_backtest -v
```

Expected: all quant tests pass.

- [ ] **Step 6: Commit**

Run:

```bash
git -C /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager add stock_screener/quant_lab/backtest.py stock_screener/tests/test_quant_backtest.py
git -C /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager commit -m "Add quant backtest runner"
```

Expected: commit includes only backtest runner and test files.

---

### Task 5: Persistence Schema And Repository

**Files:**
- Create: `stock_screener/sql/014_quant_lab.sql`
- Modify: `stock_screener/db.py`
- Test: `stock_screener/tests/test_quant_db.py`

- [ ] **Step 1: Write DB repository test**

Create `stock_screener/tests/test_quant_db.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest

from db import InMemoryQuantRepository


class QuantDbTest(unittest.TestCase):
    def test_save_and_load_backtest_result(self):
        repo = InMemoryQuantRepository()
        repo.create_quant_backtest_run({"run_id": "r1", "user_id": 7, "status": "running", "request": {"market": "US"}})
        repo.finish_quant_backtest_run("r1", {"total_return": 0.12}, warnings=["ok"])

        row = repo.get_quant_backtest_run("r1")

        self.assertEqual(row["run_id"], "r1")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["metrics"]["total_return"], 0.12)
        self.assertEqual(row["warnings"], ["ok"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_quant_db -v
```

Expected: fail because `InMemoryQuantRepository` does not exist.

- [ ] **Step 3: Add SQL migration**

Create `stock_screener/sql/014_quant_lab.sql` with concrete table definitions:

```sql
CREATE TABLE IF NOT EXISTS quant_backtest_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id VARCHAR(64) NOT NULL,
    user_id BIGINT UNSIGNED NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'running',
    request_json JSON NULL,
    rule_chain_snapshot_json JSON NULL,
    metrics_json JSON NULL,
    warnings_json JSON NULL,
    error_message TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    finished_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_quant_backtest_run_id (run_id),
    KEY idx_quant_backtest_user (user_id, created_at),
    KEY idx_quant_backtest_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='量化实验室回测任务';

CREATE TABLE IF NOT EXISTS quant_signal_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id VARCHAR(64) NOT NULL,
    signal_id VARCHAR(64) NOT NULL,
    market VARCHAR(8) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    signal_time DATETIME(6) NOT NULL,
    direction VARCHAR(16) NOT NULL,
    reason TEXT NULL,
    strength DECIMAL(10,4) NULL,
    rule_chain_key VARCHAR(128) NULL,
    rule_result_json JSON NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_quant_signal_id (signal_id),
    KEY idx_quant_signal_run (run_id),
    KEY idx_quant_signal_symbol (market, symbol, signal_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='量化信号事件';

CREATE TABLE IF NOT EXISTS quant_backtest_orders (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id VARCHAR(64) NOT NULL,
    order_id VARCHAR(64) NOT NULL,
    signal_id VARCHAR(64) NULL,
    symbol VARCHAR(32) NOT NULL,
    side VARCHAR(16) NOT NULL,
    quantity INT NOT NULL,
    order_type VARCHAR(16) NOT NULL,
    limit_price DECIMAL(20,6) NULL,
    status VARCHAR(32) NOT NULL,
    rejected_reason VARCHAR(255) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_quant_order_id (order_id),
    KEY idx_quant_order_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='量化回测模拟订单';

CREATE TABLE IF NOT EXISTS quant_backtest_trades (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id VARCHAR(64) NOT NULL,
    trade_id VARCHAR(64) NOT NULL,
    order_id VARCHAR(64) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    side VARCHAR(16) NOT NULL,
    quantity INT NOT NULL,
    price DECIMAL(20,6) NOT NULL,
    fee DECIMAL(20,6) NOT NULL DEFAULT 0,
    slippage DECIMAL(20,6) NOT NULL DEFAULT 0,
    traded_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_quant_trade_id (trade_id),
    KEY idx_quant_trade_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='量化回测模拟成交';

CREATE TABLE IF NOT EXISTS quant_equity_curve (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id VARCHAR(64) NOT NULL,
    point_time DATETIME(6) NOT NULL,
    equity DECIMAL(20,6) NOT NULL,
    cash DECIMAL(20,6) NOT NULL,
    drawdown DECIMAL(20,8) NOT NULL DEFAULT 0,
    benchmark_value DECIMAL(20,6) NULL,
    PRIMARY KEY (id),
    KEY idx_quant_equity_run (run_id, point_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='量化回测净值曲线';

CREATE TABLE IF NOT EXISTS quant_paper_accounts (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    account_id VARCHAR(64) NOT NULL,
    user_id BIGINT UNSIGNED NULL,
    name VARCHAR(128) NOT NULL,
    cash DECIMAL(20,6) NOT NULL,
    equity DECIMAL(20,6) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    config_json JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_quant_paper_account_id (account_id),
    KEY idx_quant_paper_user (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='量化纸面交易账户';
```

- [ ] **Step 4: Add in-memory repository for tests and DB method stubs**

Modify `stock_screener/db.py` near existing test-friendly repository helpers:

```python
class InMemoryQuantRepository:
    def __init__(self):
        self.runs = {}

    def create_quant_backtest_run(self, row: dict) -> None:
        self.runs[row["run_id"]] = {**row, "metrics": {}, "warnings": []}

    def finish_quant_backtest_run(self, run_id: str, metrics: dict, warnings: list[str]) -> None:
        row = self.runs[run_id]
        row["status"] = "completed"
        row["metrics"] = metrics
        row["warnings"] = warnings

    def get_quant_backtest_run(self, run_id: str) -> dict | None:
        return self.runs.get(run_id)
```

Then add MySQL methods on `MarketDatabase` following the JSON helper style already used by option tables:

```python
def create_quant_backtest_run(self, row: dict) -> None:
    self.execute(
        """
        INSERT INTO quant_backtest_runs
            (run_id, user_id, status, request_json, rule_chain_snapshot_json, warnings_json)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            row["run_id"],
            row.get("user_id"),
            row.get("status", "running"),
            _json_or_none(row.get("request") or {}),
            _json_or_none(row.get("rule_chain_snapshot") or {}),
            _json_or_none(row.get("warnings") or []),
        ),
    )
```

Add `finish_quant_backtest_run` and `get_quant_backtest_run` using the same parameter style and JSON decode helpers as existing option methods.

- [ ] **Step 5: Run DB test**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_quant_db -v
```

Expected: `Ran 1 test` and `OK`.

- [ ] **Step 6: Commit**

Run:

```bash
git -C /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager add stock_screener/sql/014_quant_lab.sql stock_screener/db.py stock_screener/tests/test_quant_db.py
git -C /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager commit -m "Add quant lab persistence schema"
```

Expected: commit includes only schema, db, and DB test files.

---

### Task 6: Quant API Router

**Files:**
- Create: `stock_screener/quant_lab/service.py`
- Create: `stock_screener/web/quant.py`
- Modify: `stock_screener/web/main.py`
- Test: `stock_screener/tests/test_quant_api.py`

- [ ] **Step 1: Write API test**

Create `stock_screener/tests/test_quant_api.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import date

from fastapi.testclient import TestClient

from web.quant import router
from fastapi import FastAPI


class QuantApiTest(unittest.TestCase):
    def test_router_exposes_backtest_schema_error_for_invalid_payload(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post("/api/quant/backtests", json={"market": "US"})

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_quant_api -v
```

Expected: fail because `web.quant` does not exist.

- [ ] **Step 3: Add service skeleton**

Create `stock_screener/quant_lab/service.py`:

```python
from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from .backtest import BacktestRequest, BacktestRunner


class QuantLabService:
    def __init__(self, repository, data_provider=None, strategy=None):
        self.repository = repository
        self.data_provider = data_provider
        self.strategy = strategy

    def submit_backtest(self, payload: dict, user_id: int | None = None) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        self.repository.create_quant_backtest_run({
            "run_id": run_id,
            "user_id": user_id,
            "status": "queued",
            "request": payload,
            "warnings": [],
        })
        return {"run_id": run_id, "status": "queued"}
```

- [ ] **Step 4: Add FastAPI router**

Create `stock_screener/web/quant.py`:

```python
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from quant_lab.service import QuantLabService

from .auth import CurrentUser, get_db, require_user
from .business import BusinessError


router = APIRouter(prefix="/api/quant", tags=["quant"])


class QuantBacktestRequest(BaseModel):
    market: str
    symbols: List[str] = Field(min_length=1)
    strategy_source: str = "rule_chain"
    entry_chain_key: str
    exit_policy: Dict[str, Any]
    start: date
    end: date
    initial_cash: float = Field(gt=0)
    quantity: int = Field(default=1, ge=1)
    commission_rate: float = Field(default=0.001, ge=0)
    slippage_rate: float = Field(default=0.001, ge=0)
    max_position_weight: float = Field(default=1.0, gt=0, le=1.0)


def get_quant_service(db=Depends(get_db)) -> QuantLabService:
    return QuantLabService(repository=db)


@router.post("/backtests")
def create_backtest(
    payload: QuantBacktestRequest,
    user: CurrentUser = Depends(require_user),
    service: QuantLabService = Depends(get_quant_service),
) -> Dict[str, Any]:
    if payload.end < payload.start:
        raise BusinessError("QUANT_INVALID_DATE_RANGE", "回测结束日期不能早于开始日期")
    return service.submit_backtest(payload.model_dump(mode="json"), user_id=user.id)
```

- [ ] **Step 5: Include router in `web/main.py`**

Modify imports:

```python
from .quant import router as quant_router
```

Modify app setup:

```python
app.include_router(options_router)
app.include_router(quant_router)
```

- [ ] **Step 6: Run API test**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_quant_api -v
```

Expected: `Ran 1 test` and `OK`.

- [ ] **Step 7: Commit**

Run:

```bash
git -C /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager add stock_screener/quant_lab/service.py stock_screener/web/quant.py stock_screener/web/main.py stock_screener/tests/test_quant_api.py
git -C /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager commit -m "Add quant lab API router"
```

Expected: commit includes only service, router, main, and API test files.

---

### Task 7: Frontend Quant Lab Page

**Files:**
- Create: `stock_screener/web_frontend/src/api.ts`
- Create: `stock_screener/web_frontend/src/features/quant/types.ts`
- Create: `stock_screener/web_frontend/src/features/quant/quantFormat.ts`
- Create: `stock_screener/web_frontend/src/features/quant/QuantLab.tsx`
- Modify: `stock_screener/web_frontend/src/main.tsx`
- Modify: `stock_screener/web_frontend/src/styles.css`

- [ ] **Step 1: Extract shared API helper**

Create `stock_screener/web_frontend/src/api.ts`:

```typescript
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options
  })
  const payload = await response.json().catch(() => null)
  if (payload?.ok === false) {
    throw new Error(payload.message || '请求失败，请稍后重试')
  }
  if (!response.ok) {
    throw new Error(payload?.detail || payload?.message || `${response.status} ${response.statusText}`)
  }
  return payload as T
}
```

Modify `src/main.tsx` to import this helper and remove the local duplicate `api<T>` function only after confirming existing calls still compile.

- [ ] **Step 2: Add quant types**

Create `stock_screener/web_frontend/src/features/quant/types.ts`:

```typescript
export type QuantBacktestRequest = {
  market: string
  symbols: string[]
  strategy_source: 'rule_chain' | 'meta_rules' | 'option_lab'
  entry_chain_key: string
  exit_policy: Record<string, unknown>
  start: string
  end: string
  initial_cash: number
  quantity: number
  commission_rate: number
  slippage_rate: number
  max_position_weight: number
}

export type QuantBacktestSubmitResponse = {
  run_id: string
  status: string
}
```

- [ ] **Step 3: Add display format helpers**

Create `stock_screener/web_frontend/src/features/quant/quantFormat.ts`:

```typescript
export function percent(value: number | undefined | null): string {
  if (value === undefined || value === null || Number.isNaN(Number(value))) return '-'
  return `${(Number(value) * 100).toFixed(2)}%`
}

export function money(value: number | undefined | null): string {
  if (value === undefined || value === null || Number.isNaN(Number(value))) return '-'
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 })
}
```

- [ ] **Step 4: Add QuantLab component**

Create `stock_screener/web_frontend/src/features/quant/QuantLab.tsx`:

```tsx
import React, { useState } from 'react'
import { api } from '../../api'
import type { QuantBacktestRequest, QuantBacktestSubmitResponse } from './types'

export function QuantLab() {
  const [market, setMarket] = useState('US')
  const [symbols, setSymbols] = useState('US.AAPL')
  const [entryChainKey, setEntryChainKey] = useState('default')
  const [exitPolicy, setExitPolicy] = useState('fixed_holding_days')
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')

  async function submit() {
    setError('')
    setStatus('提交中')
    const payload: QuantBacktestRequest = {
      market,
      symbols: symbols.split(/[,\n]/).map((item) => item.trim()).filter(Boolean),
      strategy_source: 'rule_chain',
      entry_chain_key: entryChainKey,
      exit_policy: exitPolicy === 'fixed_holding_days' ? { type: 'fixed_holding_days', days: 5 } : { type: 'stop_loss', pct: 0.08 },
      start: '2026-01-01',
      end: '2026-05-18',
      initial_cash: 100000,
      quantity: 100,
      commission_rate: 0.001,
      slippage_rate: 0.001,
      max_position_weight: 0.2
    }
    try {
      const response = await api<QuantBacktestSubmitResponse>('/api/quant/backtests', {
        method: 'POST',
        body: JSON.stringify(payload)
      })
      setStatus(`已创建回测: ${response.run_id}`)
    } catch (err) {
      setStatus('')
      setError(err instanceof Error ? err.message : '创建回测失败')
    }
  }

  return (
    <section className="quant-lab">
      <header className="page-header">
        <h1>量化实验室</h1>
        <p>使用现有规则链生成模拟交易点，回测胜率、收益、回撤和纸面交易表现。</p>
      </header>
      <div className="quant-grid">
        <aside className="panel quant-config">
          <label>市场</label>
          <select value={market} onChange={(event) => setMarket(event.target.value)}>
            <option value="US">美股</option>
            <option value="HK">港股</option>
            <option value="A">A股</option>
          </select>
          <label>标的</label>
          <textarea value={symbols} onChange={(event) => setSymbols(event.target.value)} />
          <label>入口规则链</label>
          <input value={entryChainKey} onChange={(event) => setEntryChainKey(event.target.value)} />
          <label>退出策略</label>
          <select value={exitPolicy} onChange={(event) => setExitPolicy(event.target.value)}>
            <option value="fixed_holding_days">固定持有 5 天</option>
            <option value="stop_loss">止损 8%</option>
          </select>
          <button onClick={submit}>开始回测</button>
          {status && <div className="notice">{status}</div>}
          {error && <div className="error">{error}</div>}
        </aside>
        <main className="quant-results">
          <div className="metric-row">
            <div className="metric-card">总收益<span>-</span></div>
            <div className="metric-card">最大回撤<span>-</span></div>
            <div className="metric-card">胜率<span>-</span></div>
            <div className="metric-card">盈亏比<span>-</span></div>
          </div>
          <div className="panel chart-placeholder">净值曲线 / 回撤曲线</div>
          <div className="panel chart-placeholder">交易明细 / 规则链触发明细 / 纸面交易状态</div>
        </main>
      </div>
    </section>
  )
}
```

- [ ] **Step 5: Wire page into `main.tsx`**

Modify `stock_screener/web_frontend/src/main.tsx`:

```tsx
import { QuantLab } from './features/quant/QuantLab'
```

Add a navigation button next to the existing options page button:

```tsx
<button className={page === 'quant' ? 'active' : ''} onClick={() => setPage('quant')}>量化实验室</button>
```

Add render branch:

```tsx
{page === 'quant' && <QuantLab />}
```

- [ ] **Step 6: Add CSS**

Append to `stock_screener/web_frontend/src/styles.css`:

```css
.quant-lab {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.quant-grid {
  display: grid;
  grid-template-columns: minmax(260px, 320px) 1fr;
  gap: 16px;
  align-items: start;
}

.quant-config {
  display: grid;
  gap: 10px;
}

.quant-config textarea {
  min-height: 92px;
  resize: vertical;
}

.quant-results {
  display: grid;
  gap: 16px;
}

.metric-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.metric-card {
  border: 1px solid var(--border-color, #d8dee4);
  border-radius: 8px;
  padding: 12px;
  display: flex;
  justify-content: space-between;
  gap: 8px;
}

.chart-placeholder {
  min-height: 180px;
}

@media (max-width: 900px) {
  .quant-grid,
  .metric-row {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 7: Run frontend build**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener/web_frontend
npm run build
```

Expected: build succeeds. Existing Vite chunk-size warnings are acceptable if no new TypeScript errors appear.

- [ ] **Step 8: Commit**

Run:

```bash
git -C /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager add stock_screener/web_frontend/src/api.ts stock_screener/web_frontend/src/features/quant/types.ts stock_screener/web_frontend/src/features/quant/quantFormat.ts stock_screener/web_frontend/src/features/quant/QuantLab.tsx stock_screener/web_frontend/src/main.tsx stock_screener/web_frontend/src/styles.css
git -C /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager commit -m "Add quant lab frontend page"
```

Expected: commit includes only frontend files listed above.

---

### Task 8: Paper Trading Service

**Files:**
- Create: `stock_screener/quant_lab/paper.py`
- Test: `stock_screener/tests/test_quant_paper.py`
- Modify: `stock_screener/web/quant.py`

- [ ] **Step 1: Write paper service test**

Create `stock_screener/tests/test_quant_paper.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest

from quant_lab.paper import PaperTradingService


class PaperTradingServiceTest(unittest.TestCase):
    def test_create_paper_account_does_not_place_real_orders(self):
        service = PaperTradingService(repository={})
        account = service.create_account(user_id=7, name="demo", initial_cash=100000)

        self.assertEqual(account["user_id"], 7)
        self.assertEqual(account["cash"], 100000)
        self.assertFalse(account["real_order_enabled"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_quant_paper -v
```

Expected: fail because `quant_lab.paper` does not exist.

- [ ] **Step 3: Implement paper service**

Create `stock_screener/quant_lab/paper.py`:

```python
from __future__ import annotations

import uuid


class PaperTradingService:
    def __init__(self, repository):
        self.repository = repository

    def create_account(self, user_id: int | None, name: str, initial_cash: float) -> dict:
        return {
            "account_id": str(uuid.uuid4()),
            "user_id": user_id,
            "name": name,
            "cash": float(initial_cash),
            "equity": float(initial_cash),
            "status": "active",
            "real_order_enabled": False,
        }
```

- [ ] **Step 4: Add paper account API**

Modify `stock_screener/web/quant.py`:

```python
from quant_lab.paper import PaperTradingService


class CreatePaperAccountRequest(BaseModel):
    name: str = "默认模拟账户"
    initial_cash: float = Field(gt=0)


@router.post("/paper/accounts")
def create_paper_account(
    payload: CreatePaperAccountRequest,
    user: CurrentUser = Depends(require_user),
    db=Depends(get_db),
) -> Dict[str, Any]:
    service = PaperTradingService(repository=db)
    return service.create_account(user_id=user.id, name=payload.name, initial_cash=payload.initial_cash)
```

- [ ] **Step 5: Run paper tests**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_quant_paper tests.test_quant_api -v
```

Expected: paper and API tests pass.

- [ ] **Step 6: Commit**

Run:

```bash
git -C /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager add stock_screener/quant_lab/paper.py stock_screener/tests/test_quant_paper.py stock_screener/web/quant.py
git -C /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager commit -m "Add quant paper trading service"
```

Expected: commit includes only paper service, paper tests, and quant router update.

---

### Task 9: Limited Option Lab Replay

**Files:**
- Create: `stock_screener/quant_lab/option_adapter.py`
- Test: `stock_screener/tests/test_quant_option_adapter.py`

- [ ] **Step 1: Write option adapter test**

Create `stock_screener/tests/test_quant_option_adapter.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import date

from quant_lab.option_adapter import OptionCandidateReplayAdapter


class OptionCandidateReplayAdapterTest(unittest.TestCase):
    def test_missing_option_snapshot_returns_limited_warning(self):
        adapter = OptionCandidateReplayAdapter(snapshot_loader=lambda run_id: None)
        result = adapter.replay(candidate_id="c1", run_id="r1", replay_date=date(2026, 1, 2))

        self.assertEqual(result["mode"], "limited_option_replay")
        self.assertIn("missing_option_snapshot", result["warnings"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_quant_option_adapter -v
```

Expected: fail because `quant_lab.option_adapter` does not exist.

- [ ] **Step 3: Implement limited replay adapter**

Create `stock_screener/quant_lab/option_adapter.py`:

```python
from __future__ import annotations

from datetime import date
from typing import Callable


class OptionCandidateReplayAdapter:
    def __init__(self, snapshot_loader: Callable[[str], dict | None]):
        self.snapshot_loader = snapshot_loader

    def replay(self, candidate_id: str, run_id: str, replay_date: date) -> dict:
        snapshot = self.snapshot_loader(run_id)
        if not snapshot:
            return {
                "candidate_id": candidate_id,
                "run_id": run_id,
                "replay_date": replay_date.isoformat(),
                "mode": "limited_option_replay",
                "warnings": ["missing_option_snapshot"],
                "signals": [],
            }
        return {
            "candidate_id": candidate_id,
            "run_id": run_id,
            "replay_date": replay_date.isoformat(),
            "mode": "limited_option_replay",
            "warnings": [],
            "snapshot": snapshot,
        }
```

- [ ] **Step 4: Run option adapter test**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_quant_option_adapter -v
```

Expected: `Ran 1 test` and `OK`.

- [ ] **Step 5: Commit**

Run:

```bash
git -C /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager add stock_screener/quant_lab/option_adapter.py stock_screener/tests/test_quant_option_adapter.py
git -C /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager commit -m "Add limited option replay adapter"
```

Expected: commit includes only option adapter and test files.

---

### Task 10: Final Verification

**Files:**
- No new files.
- Verify all changed backend and frontend surfaces.

- [ ] **Step 1: Run quant tests**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_quant_metrics \
  tests.test_quant_broker \
  tests.test_quant_rule_chain_adapter \
  tests.test_quant_backtest \
  tests.test_quant_db \
  tests.test_quant_api \
  tests.test_quant_paper \
  tests.test_quant_option_adapter \
  -v
```

Expected: all Quant Lab tests pass.

- [ ] **Step 2: Run existing related tests**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_rule_engine tests.test_option_lab_service tests.test_option_lab_api -v
```

Expected: existing rule engine and Option Lab tests pass.

- [ ] **Step 3: Run frontend build**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener/web_frontend
npm run build
```

Expected: build succeeds with no TypeScript errors.

- [ ] **Step 4: Confirm no search or LLM calls in default backtest path**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager
rg -n "search_providers|llm_providers|SIGNAL_ENABLE_EVIDENCE_EXPANSION|OpenAI|LLM" stock_screener/quant_lab stock_screener/web/quant.py
```

Expected: no matches in `stock_screener/quant_lab` or `stock_screener/web/quant.py`.

- [ ] **Step 5: Inspect git status before final handoff**

Run:

```bash
git -C /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager status --short
```

Expected: only pre-existing user changes remain, or no changes remain if each task was committed. `.superpowers/` must not be staged.

- [ ] **Step 6: Final commit if verification adjusted files**

If Step 1 through Step 4 required fixes, stage only files changed by those fixes and commit:

```bash
git -C /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager add <exact files changed by verification fixes>
git -C /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager commit -m "Verify quant lab implementation"
```

Expected: no unrelated user changes are included.

---

## Self-Review

- Spec coverage:
  - Unified stock/options framework: covered by shared `quant_lab` models, backtest runner, rule-chain adapter, and option adapter tasks.
  - Rule-chain-first design: covered by Task 3 and Task 4.
  - Simulated trading points from buy/sell signals: covered by Task 2 and Task 4.
  - Metrics and win-rate analysis: covered by Task 1.
  - Paper trading without real orders: covered by Task 8.
  - Frontend page: covered by Task 7.
  - API and persistence: covered by Task 5 and Task 6.
  - Cost control: covered by Task 10.
- Placeholder scan:
  - This plan avoids placeholder markers, generic edge-case instructions, and undefined task references.
- Type consistency:
  - `Signal`, `Order`, `Trade`, `EquityPoint`, and `MetricSnapshot` are defined before consumers use them.
  - API request names and frontend request names both use `strategy_source`, `entry_chain_key`, `exit_policy`, `initial_cash`, `commission_rate`, `slippage_rate`, and `max_position_weight`.
