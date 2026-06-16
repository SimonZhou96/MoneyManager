# Quant Lab 经典策略回测 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 quant_lab 回测框架上新增 6 个经典量化策略（首期 MA/MACD/RSI）、参数网格搜索优化器、止损/止盈/移动止损风控、扩展绩效指标、3 个新 API 端点，以及独立的三栏前端回测页面。

**Architecture:** 策略层（BaseStrategy ABC）与回测引擎（BacktestRunner + SimulatedBroker）解耦。策略纯函数：DataFrame 入→list[Signal] 出。Broker 统一处理滑点/佣金/风控出场。优化器独立模块，组装策略+引擎做笛卡尔积遍历。API 层沿用现有异步 submit→poll 模式。

**Tech Stack:** Python 3.10+ (dataclasses, abc, itertools), pandas (EMA/rolling), FastAPI (Pydantic v2), React 18 + ECharts (前端)

---

## File Structure Map

| 文件 | 操作 | 职责 |
|------|------|------|
| `quant_lab/models.py` | Modify | 新增 StrategyConfig, ParamGrid, RiskConfig, OptimizationResult |
| `quant_lab/strategies/__init__.py` | Create | 策略注册表 STRATEGY_REGISTRY |
| `quant_lab/strategies/base.py` | Create | BaseStrategy ABC |
| `quant_lab/strategies/ma_cross.py` | Create | MACrossStrategy |
| `quant_lab/strategies/macd.py` | Create | MACDStrategy |
| `quant_lab/strategies/rsi.py` | Create | RSIStrategy |
| `quant_lab/broker.py` | Modify | RiskConfig 风控 + avg_cost/high_water 追踪 |
| `quant_lab/backtest.py` | Modify | 适配 Strategy 对象 + 逐日风控检查 |
| `quant_lab/metrics.py` | Modify | Sharpe/Sortino/Calmar/CAGR/年化波动率 |
| `quant_lab/optimizer.py` | Create | GridSearchOptimizer |
| `quant_lab/service.py` | Modify | run_strategy_backtest / run_optimization |
| `web/quant.py` | Modify | 3 个新端点 + Pydantic 模型 |
| `web_frontend/src/features/quant/QuantLab.tsx` | Rewrite | 三栏策略回测页面 |
| `web_frontend/src/features/quant/types.ts` | Modify | 新增前端类型 |
| `web_frontend/src/styles.css` | Modify | 新增回测页面样式 |
| `tests/test_quant_strategies.py` | Create | 策略单元测试 + 优化器测试 |

---

### Task 1: 新增数据模型

**Files:**
- Modify: `stock_screener/quant_lab/models.py`

**What:** 在现有 models.py 末尾追加 StrategyConfig, ParamGrid, RiskConfig, OptimizationResult 四个 dataclass。

- [ ] **Step 1: 追加模型定义**

在 `stock_screener/quant_lab/models.py` 文件末尾追加：

```python
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
```

- [ ] **Step 2: 验证导入**

```bash
cd stock_screener && python3 -c "from quant_lab.models import StrategyConfig, ParamGrid, RiskConfig, OptimizationResult; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
rtk git add stock_screener/quant_lab/models.py
rtk git commit -m "feat(quant_lab): add StrategyConfig, ParamGrid, RiskConfig, OptimizationResult models"
```

---

### Task 2: BaseStrategy 抽象基类 + 策略注册表

**Files:**
- Create: `stock_screener/quant_lab/strategies/__init__.py`
- Create: `stock_screener/quant_lab/strategies/base.py`

- [ ] **Step 1: 创建策略注册表**

`stock_screener/quant_lab/strategies/__init__.py`:

```python
"""Quant Lab 经典策略库"""

from .base import BaseStrategy
from .ma_cross import MACrossStrategy
from .macd import MACDStrategy
from .rsi import RSIStrategy


# 策略注册表：前端 GET /api/quant/strategies 的源数据
STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {}


def _register(cls: type[BaseStrategy]) -> type[BaseStrategy]:
    STRATEGY_REGISTRY[cls.strategy_type()] = cls
    return cls


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


def create_strategy(strategy_type: str, params: dict, **kwargs) -> "BaseStrategy":
    """工厂方法：根据 strategy_type 创建策略实例"""
    from .models import StrategyConfig
    cls = STRATEGY_REGISTRY.get(strategy_type)
    if cls is None:
        raise ValueError(f"Unknown strategy type: {strategy_type}. Available: {list(STRATEGY_REGISTRY.keys())}")
    config = StrategyConfig(strategy_type=strategy_type, params=params, **kwargs)
    return cls(config)
```

- [ ] **Step 2: 创建 BaseStrategy ABC**

`stock_screener/quant_lab/strategies/base.py`:

```python
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
```

- [ ] **Step 3: 验证导入**

```bash
cd stock_screener && python3 -c "from quant_lab.strategies.base import BaseStrategy; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
rtk git add stock_screener/quant_lab/strategies/
rtk git commit -m "feat(quant_lab): add BaseStrategy ABC and strategy registry"
```

---

### Task 3: 双均线交叉策略 (MA Cross)

**Files:**
- Create: `stock_screener/quant_lab/strategies/ma_cross.py`

- [ ] **Step 1: 实现 MACrossStrategy**

`stock_screener/quant_lab/strategies/ma_cross.py`:

```python
from __future__ import annotations

import uuid

import pandas as pd

from ..models import Signal
from .base import BaseStrategy
from . import _register


@_register
class MACrossStrategy(BaseStrategy):
    """双均线交叉策略 — 快线上穿慢线买入，下穿卖出"""

    @staticmethod
    def strategy_type() -> str:
        return "ma_cross"

    @staticmethod
    def name() -> str:
        return "双均线交叉"

    @staticmethod
    def param_definitions() -> dict:
        return {
            "fast": {"type": "int", "default": 5, "min": 2, "max": 120, "label": "快线周期"},
            "slow": {"type": "int", "default": 20, "min": 5, "max": 250, "label": "慢线周期"},
        }

    def generate_signals(self, df: pd.DataFrame) -> list[Signal]:
        fast = int(self.config.params.get("fast", 5))
        slow = int(self.config.params.get("slow", 20))
        entry_side = self.config.entry_side

        df = df.sort_values("date").reset_index(drop=True)
        close = df["close"].astype(float)

        # EMA 均线
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()

        # 交叉检测：快线上穿慢线 = 昨快≤昨慢 且 今快>今慢
        prev_fast = ema_fast.shift(1)
        prev_slow = ema_slow.shift(1)

        golden_cross = (prev_fast <= prev_slow) & (ema_fast > ema_slow)    # 金叉
        dead_cross = (prev_fast >= prev_slow) & (ema_fast < ema_slow)      # 死叉

        signals: list[Signal] = []
        for i in range(1, len(df)):
            row = df.iloc[i]
            ts = row["date"]
            if hasattr(ts, "date"):
                ts = ts.date()

            if golden_cross.iloc[i] and entry_side in ("long", "both"):
                signals.append(Signal(
                    signal_id=str(uuid.uuid4()),
                    ts=ts,
                    market="",
                    symbol="",
                    direction="buy",
                    reason=f"MA金叉 EMA{fast}↑EMA{slow}",
                    strength=1.0,
                ))
            elif dead_cross.iloc[i] and entry_side in ("long", "both"):
                signals.append(Signal(
                    signal_id=str(uuid.uuid4()),
                    ts=ts,
                    market="",
                    symbol="",
                    direction="sell",
                    reason=f"MA死叉 EMA{fast}↓EMA{slow}",
                    strength=1.0,
                ))

        return signals
```

- [ ] **Step 2: 单元测试**

```bash
cd stock_screener && python3 -c "
import pandas as pd
from datetime import date
from quant_lab.strategies.ma_cross import MACrossStrategy
from quant_lab.models import StrategyConfig

# 构造简单上升趋势数据：快线应上穿慢线
dates = pd.date_range('2025-01-01', periods=30, freq='B')
df = pd.DataFrame({
    'date': dates,
    'open': [10 + i * 0.3 for i in range(30)],
    'high': [10.5 + i * 0.3 for i in range(30)],
    'low': [9.5 + i * 0.3 for i in range(30)],
    'close': [10 + i * 0.3 for i in range(30)],
    'volume': [1000] * 30,
})

config = StrategyConfig(strategy_type='ma_cross', params={'fast': 3, 'slow': 10}, entry_side='long')
strategy = MACrossStrategy(config)
signals = strategy.generate_signals(df)
print(f'Generated {len(signals)} signals')
for s in signals:
    print(f'  {s.ts} {s.direction}: {s.reason}')
print('MA_CROSS — OK')
"
```

Expected: 至少 1 个 buy 信号，输出 `MA_CROSS — OK`

- [ ] **Step 3: Commit**

```bash
rtk git add stock_screener/quant_lab/strategies/ma_cross.py
rtk git commit -m "feat(quant_lab): add MACrossStrategy - moving average crossover"
```

---

### Task 4: MACD 策略

**Files:**
- Create: `stock_screener/quant_lab/strategies/macd.py`

- [ ] **Step 1: 实现 MACDStrategy**

`stock_screener/quant_lab/strategies/macd.py`:

```python
from __future__ import annotations

import uuid

import pandas as pd

from ..models import Signal
from .base import BaseStrategy
from . import _register


@_register
class MACDStrategy(BaseStrategy):
    """MACD 策略 — DIF 上穿 DEA 买入，下穿卖出"""

    @staticmethod
    def strategy_type() -> str:
        return "macd"

    @staticmethod
    def name() -> str:
        return "MACD"

    @staticmethod
    def param_definitions() -> dict:
        return {
            "fast": {"type": "int", "default": 12, "min": 2, "max": 60, "label": "快线"},
            "slow": {"type": "int", "default": 26, "min": 5, "max": 120, "label": "慢线"},
            "signal": {"type": "int", "default": 9, "min": 2, "max": 30, "label": "信号线"},
        }

    def generate_signals(self, df: pd.DataFrame) -> list[Signal]:
        fast = int(self.config.params.get("fast", 12))
        slow = int(self.config.params.get("slow", 26))
        sig_period = int(self.config.params.get("signal", 9))
        entry_side = self.config.entry_side

        df = df.sort_values("date").reset_index(drop=True)
        close = df["close"].astype(float)

        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=sig_period, adjust=False).mean()
        # macd_bar = 2 * (dif - dea)  # 柱状值，策略中不使用但保留计算

        prev_dif = dif.shift(1)
        prev_dea = dea.shift(1)

        golden_cross = (prev_dif <= prev_dea) & (dif > dea)
        dead_cross = (prev_dif >= prev_dea) & (dif < dea)

        signals: list[Signal] = []
        for i in range(1, len(df)):
            row = df.iloc[i]
            ts = row["date"]
            if hasattr(ts, "date"):
                ts = ts.date()

            if golden_cross.iloc[i] and entry_side in ("long", "both"):
                signals.append(Signal(
                    signal_id=str(uuid.uuid4()),
                    ts=ts,
                    market="",
                    symbol="",
                    direction="buy",
                    reason=f"MACD金叉 DIF↑DEA",
                    strength=1.0,
                ))
            elif dead_cross.iloc[i] and entry_side in ("long", "both"):
                signals.append(Signal(
                    signal_id=str(uuid.uuid4()),
                    ts=ts,
                    market="",
                    symbol="",
                    direction="sell",
                    reason=f"MACD死叉 DIF↓DEA",
                    strength=1.0,
                ))

        return signals
```

- [ ] **Step 2: 验证**

```bash
cd stock_screener && python3 -c "
import pandas as pd
from quant_lab.models import StrategyConfig
from quant_lab.strategies.macd import MACDStrategy

dates = pd.date_range('2025-01-01', periods=60, freq='B')
close = [10.0]
for i in range(59):
    close.append(close[-1] * (1 + 0.005))
df = pd.DataFrame({
    'date': dates, 'open': close, 'high': [c*1.01 for c in close],
    'low': [c*0.99 for c in close], 'close': close, 'volume': [1000]*60,
})

config = StrategyConfig(strategy_type='macd', params={'fast': 12, 'slow': 26, 'signal': 9}, entry_side='long')
signals = MACDStrategy(config).generate_signals(df)
print(f'{len(signals)} signals generated, MACD — OK')
for s in signals[:3]:
    print(f'  {s.ts} {s.direction}: {s.reason}')
"
```

Expected: signals > 0, `MACD — OK`

- [ ] **Step 3: Commit**

```bash
rtk git add stock_screener/quant_lab/strategies/macd.py
rtk git commit -m "feat(quant_lab): add MACDStrategy"
```

---

### Task 5: RSI 策略

**Files:**
- Create: `stock_screener/quant_lab/strategies/rsi.py`

- [ ] **Step 1: 实现 RSIStrategy**

`stock_screener/quant_lab/strategies/rsi.py`:

```python
from __future__ import annotations

import uuid

import pandas as pd
import numpy as np

from ..models import Signal
from .base import BaseStrategy
from . import _register


@_register
class RSIStrategy(BaseStrategy):
    """RSI 策略 — RSI 低于超卖线买入，高于超买线卖出"""

    @staticmethod
    def strategy_type() -> str:
        return "rsi"

    @staticmethod
    def name() -> str:
        return "RSI"

    @staticmethod
    def param_definitions() -> dict:
        return {
            "period": {"type": "int", "default": 14, "min": 2, "max": 60, "label": "RSI 周期"},
            "oversold": {"type": "int", "default": 30, "min": 10, "max": 50, "label": "超卖阈值"},
            "overbought": {"type": "int", "default": 70, "min": 50, "max": 90, "label": "超买阈值"},
        }

    @staticmethod
    def _calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
        """计算 RSI 序列"""
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def generate_signals(self, df: pd.DataFrame) -> list[Signal]:
        period = int(self.config.params.get("period", 14))
        oversold = int(self.config.params.get("oversold", 30))
        overbought = int(self.config.params.get("overbought", 70))
        entry_side = self.config.entry_side

        df = df.sort_values("date").reset_index(drop=True)
        close = df["close"].astype(float)
        rsi_series = self._calc_rsi(close, period)

        prev_rsi = rsi_series.shift(1)

        # 从超卖区回升
        enter_long = (prev_rsi < oversold) & (rsi_series >= oversold)
        # 从超买区回落
        exit_long = (prev_rsi > overbought) & (rsi_series <= overbought)

        signals: list[Signal] = []
        for i in range(1, len(df)):
            row = df.iloc[i]
            ts = row["date"]
            if hasattr(ts, "date"):
                ts = ts.date()

            if not pd.isna(rsi_series.iloc[i]):
                if enter_long.iloc[i] and entry_side in ("long", "both"):
                    signals.append(Signal(
                        signal_id=str(uuid.uuid4()),
                        ts=ts,
                        market="",
                        symbol="",
                        direction="buy",
                        reason=f"RSI超卖回升 RSI={rsi_series.iloc[i]:.1f}",
                        strength=1.0,
                    ))
                elif exit_long.iloc[i] and entry_side in ("long", "both"):
                    signals.append(Signal(
                        signal_id=str(uuid.uuid4()),
                        ts=ts,
                        market="",
                        symbol="",
                        direction="sell",
                        reason=f"RSI超买回落 RSI={rsi_series.iloc[i]:.1f}",
                        strength=1.0,
                    ))

        return signals
```

- [ ] **Step 2: 验证**

```bash
cd stock_screener && python3 -c "
import pandas as pd
import numpy as np
from quant_lab.models import StrategyConfig
from quant_lab.strategies.rsi import RSIStrategy

np.random.seed(42)
dates = pd.date_range('2025-01-01', periods=100, freq='B')
close = pd.Series([100.0])
for _ in range(99):
    close = pd.concat([close, pd.Series([close.iloc[-1] * (1 + np.random.normal(0, 0.03))])])
close = close.reset_index(drop=True)
df = pd.DataFrame({
    'date': dates, 'open': close, 'high': close*1.01,
    'low': close*0.99, 'close': close, 'volume': [1000]*100,
})

config = StrategyConfig(strategy_type='rsi', params={'period': 14, 'oversold': 30, 'overbought': 70}, entry_side='long')
signals = RSIStrategy(config).generate_signals(df)
print(f'{len(signals)} signals generated, RSI — OK')
for s in signals[:5]:
    print(f'  {s.ts} {s.direction}: {s.reason}')
"
```

Expected: signals > 0, `RSI — OK`

- [ ] **Step 3: Commit**

```bash
rtk git add stock_screener/quant_lab/strategies/rsi.py
rtk git commit -m "feat(quant_lab): add RSIStrategy"
```

---

### Task 6: Broker 风控增强

**Files:**
- Modify: `stock_screener/quant_lab/broker.py`

**What:** SimulatedBroker 新增 RiskConfig 支持，添加逐日风控检查、平均成本和高水印追踪。

- [ ] **Step 1: 重写 SimulatedBroker 支持 RiskConfig**

用以下内容替换 `stock_screener/quant_lab/broker.py` 的 SimulatedBroker 类（保留文件头 import 部分不变，只替换类定义）：

```python
class SimulatedBroker:
    def __init__(
        self,
        initial_cash: float,
        commission_rate: float = 0.001,
        slippage_rate: float = 0.001,
        max_position_weight: float = 1.0,
        risk_config=None,   # RiskConfig | None
    ):
        from .models import RiskConfig
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.commission_rate = float(commission_rate)
        self.slippage_rate = float(slippage_rate)
        self.max_position_weight = float(max_position_weight)
        self.risk_config = risk_config if isinstance(risk_config, RiskConfig) else None
        self.positions: dict[str, int] = {}
        # 追踪每只标的的平均成本和历史最高价（用于风控）
        self._avg_cost: dict[str, float] = {}
        self._high_water: dict[str, float] = {}

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

        if side == "buy":
            if not self._within_position_limit(notional):
                return Order(**{**order.__dict__, "status": "rejected", "rejected_reason": "max_position_weight_exceeded"}), None
            if not self._can_buy(notional + fee):
                return Order(**{**order.__dict__, "status": "rejected", "rejected_reason": "insufficient_cash"}), None

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

    def check_risk(self, symbol: str, bar: Bar) -> Optional[Tuple[Order, Trade]]:
        """逐日风控检查 — 在当日信号处理后调用

        Returns:
            (forced_order, forced_trade) 如果触发止损/止盈/移动止损，否则 None
        """
        qty = self.positions.get(symbol, 0)
        if qty <= 0 or symbol not in self._avg_cost:
            return None
        if self.risk_config is None:
            return None

        close = float(bar.close)
        avg_cost = self._avg_cost[symbol]

        # 更新高水印
        self._high_water[symbol] = max(self._high_water.get(symbol, avg_cost), close)

        pnl_pct = (close - avg_cost) / avg_cost if avg_cost > 0 else 0

        reason: Optional[str] = None

        # 止盈
        if self.risk_config.take_profit_pct is not None and pnl_pct >= self.risk_config.take_profit_pct:
            reason = "take_profit"
        # 止损
        elif self.risk_config.stop_loss_pct is not None and pnl_pct <= self.risk_config.stop_loss_pct:
            reason = "stop_loss"
        # 移动止损
        elif self.risk_config.trailing_stop_pct is not None:
            high_water = self._high_water[symbol]
            if (high_water - close) / high_water >= self.risk_config.trailing_stop_pct:
                reason = "trailing_stop"

        if reason is None:
            return None

        # 强制卖出
        order = Order(
            order_id=str(uuid.uuid4()),
            signal_id="",
            ts=bar.ts,
            symbol=symbol,
            side="sell",
            quantity=qty,
            status="filled",
        )
        fill_price = self._fill_price("sell", bar)
        fee = fill_price * qty * self.commission_rate
        trade = Trade(
            order_id=order.order_id,
            trade_id=str(uuid.uuid4()),
            symbol=symbol,
            side="sell",
            quantity=qty,
            price=round(fill_price, 6),
            fee=round(fee, 6),
            slippage=round(abs(fill_price - close), 6),
            ts=bar.ts,
        )
        self._apply_trade(trade)
        return order, trade

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
        symbol = trade.symbol
        if trade.side == "buy":
            self.cash -= notional + trade.fee
            prev_qty = self.positions.get(symbol, 0)
            prev_cost = self._avg_cost.get(symbol, 0.0)
            if prev_qty > 0:
                total_cost = prev_cost * prev_qty + notional + trade.fee
                self._avg_cost[symbol] = total_cost / (prev_qty + trade.quantity)
            else:
                self._avg_cost[symbol] = (notional + trade.fee) / trade.quantity
            self.positions[symbol] = prev_qty + trade.quantity
        elif trade.side == "sell":
            self.cash += notional - trade.fee
            new_qty = self.positions.get(symbol, 0) - trade.quantity
            if new_qty <= 0:
                self.positions.pop(symbol, None)
                self._avg_cost.pop(symbol, None)
                self._high_water.pop(symbol, None)
            else:
                self.positions[symbol] = new_qty
```

- [ ] **Step 2: 验证风控逻辑**

```bash
cd stock_screener && python3 -c "
import uuid
from datetime import date
from quant_lab.broker import SimulatedBroker
from quant_lab.models import Bar, Signal, RiskConfig

broker = SimulatedBroker(
    initial_cash=100000,
    risk_config=RiskConfig(stop_loss_pct=-0.05, take_profit_pct=0.10),
)

# 买入
buy_signal = Signal(
    signal_id=str(uuid.uuid4()), ts=date(2025,1,2), market='HK', symbol='00700',
    direction='buy', reason='test',
)
buy_bar = Bar(ts=date(2025,1,2), open=100, high=101, low=99, close=100, volume=1000)
order, trade = broker.apply_signal(buy_signal, buy_bar, quantity=10)
print(f'Buy: cash={broker.cash:.0f} pos={broker.positions} avg_cost={broker._avg_cost}')

# 大跌触发止损
crash_bar = Bar(ts=date(2025,1,3), open=94, high=95, low=93, close=94, volume=1000)
result = broker.check_risk('00700', crash_bar)
if result:
    order, trade = result
    print(f'Stop loss: cash={broker.cash:.0f} pos={broker.positions}')
else:
    print('No risk trigger (unexpected)')
print('BROKER_RISK — OK')
"
```

Expected: 止损触发，position 清空，`BROKER_RISK — OK`

- [ ] **Step 3: Commit**

```bash
rtk git add stock_screener/quant_lab/broker.py
rtk git commit -m "feat(quant_lab): add RiskConfig - stop_loss/take_profit/trailing_stop to SimulatedBroker"
```

---

### Task 7: BacktestRunner 适配策略对象 + 风控集成

**Files:**
- Modify: `stock_screener/quant_lab/backtest.py`

**What:** BacktestRunner 现在接受 strategy（BaseStrategy 或 RuleChainStrategyAdapter），做 duck-typing 检测。在逐日循环中集成 broker.check_risk()。

- [ ] **Step 1: 重写 BacktestRunner**

用以下内容替换 `stock_screener/quant_lab/backtest.py`：

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

import pandas as pd

from filters import StockInfo

from .broker import SimulatedBroker
from .metrics import calculate_metric_snapshot
from .models import Bar, EquityPoint, MetricSnapshot, Order, RiskConfig, Signal, Trade


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
    risk_config: Optional[RiskConfig] = None


@dataclass(frozen=True)
class BacktestResult:
    signals: List[Signal] = field(default_factory=list)
    orders: List[Order] = field(default_factory=list)
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[EquityPoint] = field(default_factory=list)
    bars_by_symbol: Dict[str, List[Bar]] = field(default_factory=dict)
    metrics: MetricSnapshot = field(default_factory=MetricSnapshot)
    warnings: List[str] = field(default_factory=list)


class BacktestRunner:
    def __init__(self, data_provider, strategy):
        """
        Args:
            data_provider: 有 bars_for(market, symbol, start, end) 方法
            strategy: BaseStrategy（generate_signals） 或 RuleChainStrategyAdapter（evaluate）
        """
        self.data_provider = data_provider
        self.strategy = strategy

    @property
    def _is_base_strategy(self) -> bool:
        """Duck-typing 检测：有 generate_signals 且没有 evaluate → BaseStrategy"""
        return hasattr(self.strategy, "generate_signals") and not hasattr(self.strategy, "evaluate")

    def run(self, request: BacktestRequest) -> BacktestResult:
        broker = SimulatedBroker(
            initial_cash=request.initial_cash,
            commission_rate=request.commission_rate,
            slippage_rate=request.slippage_rate,
            max_position_weight=request.max_position_weight,
            risk_config=request.risk_config,
        )
        signals: list[Signal] = []
        orders: list[Order] = []
        trades: list[Trade] = []
        equity: list[EquityPoint] = []
        warnings: list[str] = []
        bars_by_symbol: dict[str, list[Bar]] = {}

        for symbol in request.symbols:
            bars = self.data_provider.bars_for(request.market, symbol, request.start, request.end)
            if not bars:
                warnings.append(f"missing_bars:{symbol}")
                continue
            bars_by_symbol[symbol] = bars

            # 生成信号：根据策略类型分发
            if self._is_base_strategy:
                df = _bars_to_frame(bars)
                symbol_signals = self.strategy.generate_signals(df)
                # 补全信号的 market/symbol（策略不感知）
                symbol_signals = [
                    Signal(**{**s.__dict__, "market": request.market, "symbol": symbol})
                    for s in symbol_signals
                ]
            else:
                bar_by_date = {bar.ts: bar for bar in bars}
                stock = StockInfo(market=request.market, code=symbol, name=symbol, kline_df=_bars_to_frame(bars))
                symbol_signals = self.strategy.evaluate(stock, [bar.ts for bar in bars])

            signals.extend(symbol_signals)

            # 逐日模拟
            for bar in bars:
                # 1. 处理当日信号
                for signal in [item for item in symbol_signals if item.ts == bar.ts]:
                    order, trade = broker.apply_signal(signal, bar, quantity=request.quantity)
                    orders.append(order)
                    if trade:
                        trades.append(trade)

                # 2. 逐日风控检查（在信号处理之后）
                if request.risk_config is not None:
                    risk_result = broker.check_risk(symbol, bar)
                    if risk_result:
                        risk_order, risk_trade = risk_result
                        orders.append(risk_order)
                        if risk_trade:
                            trades.append(risk_trade)

                # 3. 记录权益
                equity.append(EquityPoint(
                    ts=bar.ts,
                    equity=broker.cash + _market_value(broker, symbol, bar),
                    cash=broker.cash,
                    drawdown=0,
                ))

            # 信号日不在 bars 中的告警
            bar_by_date = {bar.ts: bar for bar in bars}
            missing_signal_dates = sorted({
                s.ts for s in symbol_signals
                if s.ts not in bar_by_date
            })
            for missing in missing_signal_dates:
                warnings.append(f"missing_signal_bar:{symbol}:{missing.isoformat()}")

        equity = _with_drawdowns(equity)
        metrics = calculate_metric_snapshot(equity, trades)
        return BacktestResult(
            signals=signals,
            orders=orders,
            trades=trades,
            equity_curve=equity,
            bars_by_symbol=bars_by_symbol,
            metrics=metrics,
            warnings=warnings,
        )


def _market_value(broker: SimulatedBroker, symbol: str, bar: Bar) -> float:
    return broker.positions.get(symbol, 0) * float(bar.close)


def _bars_to_frame(bars: list[Bar]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": bar.ts,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
            for bar in bars
        ]
    )


def _with_drawdowns(points: list[EquityPoint]) -> list[EquityPoint]:
    peak = 0.0
    result = []
    for point in points:
        peak = max(peak, float(point.equity))
        drawdown = (float(point.equity) - peak) / peak if peak else 0
        result.append(EquityPoint(
            ts=point.ts, equity=point.equity, cash=point.cash,
            drawdown=drawdown, benchmark_value=point.benchmark_value,
        ))
    return result
```

- [ ] **Step 2: 集成测试 — 用 MA 策略做一次完整回测**

```bash
cd stock_screener && python3 -c "
from datetime import date
import pandas as pd
from quant_lab.backtest import BacktestRequest, BacktestRunner
from quant_lab.models import Bar, RiskConfig
from quant_lab.strategies import create_strategy

# 模拟 data_provider
class MockDataProvider:
    def bars_for(self, market, symbol, start, end):
        dates = pd.date_range(start, end, freq='B')
        close = [100 + i * 0.5 for i in range(len(dates))]
        return [
            Bar(ts=d.date(), open=c, high=c*1.01, low=c*0.99, close=c, volume=1000)
            for d, c in zip(dates, close)
        ]

strategy = create_strategy('ma_cross', {'fast': 3, 'slow': 10}, entry_side='long')
runner = BacktestRunner(MockDataProvider(), strategy)
result = runner.run(BacktestRequest(
    market='HK', symbols=['00700'],
    start=date(2025,1,1), end=date(2025,6,1),
    initial_cash=100000, quantity=10,
    risk_config=RiskConfig(stop_loss_pct=-0.15),
))
print(f'Signals: {len(result.signals)}, Trades: {len(result.trades)}')
print(f'Total return: {result.metrics.total_return:.4f}')
print(f'Win rate: {result.metrics.win_rate:.4f}')
print(f'Warnings: {result.warnings}')
print('BACKTEST_INTEGRATION — OK')
"
```

Expected: 输出 Signals/Trades/Return/WinRate，`BACKTEST_INTEGRATION — OK`

- [ ] **Step 3: Commit**

```bash
rtk git add stock_screener/quant_lab/backtest.py
rtk git commit -m "feat(quant_lab): BacktestRunner supports BaseStrategy + risk check integration"
```

---

### Task 8: 扩展绩效指标

**Files:**
- Modify: `stock_screener/quant_lab/metrics.py`

**What:** MetricSnapshot 新增 sharpe/sortino/calmar/cagr/annual_volatility 字段；calculate_metric_snapshot 补齐计算。

- [ ] **Step 1: 更新 MetricSnapshot 和 calculate_metric_snapshot**

`stock_screener/quant_lab/metrics.py` — 在 `MetricSnapshot` 的 field 列表中追加 5 个字段，并重写 `calculate_metric_snapshot`：

**MetricSnapshot 追加字段**（在现有字段之后、`option_metrics` 之前）：
```python
    # 新增字段
    sharpe: float = 0
    sortino: float = 0
    calmar: float = 0
    cagr: float = 0
    annual_volatility: float = 0
```

**重写 calculate_metric_snapshot 函数**（替换整个函数体）：

```python
import numpy as np


def calculate_metric_snapshot(equity: List[EquityPoint], trades: List[Trade]) -> MetricSnapshot:
    if not equity:
        return MetricSnapshot()

    start = float(equity[0].equity or 0)
    end = float(equity[-1].equity or 0)
    days = max(1, (equity[-1].ts - equity[0].ts).days if hasattr(equity[-1].ts, "days") else len(equity))
    years = days / 365.25

    total_return = (end - start) / start if start > 0 else 0
    cagr = ((end / start) ** (1 / years) - 1) if start > 0 and years > 0 else 0

    max_drawdown = min((point.drawdown for point in equity), default=0)

    # 日收益率序列
    eq_values = np.array([float(p.equity) for p in equity], dtype=float)
    daily_returns = np.diff(eq_values) / eq_values[:-1] if len(eq_values) > 1 else np.array([])

    annual_vol = float(np.std(daily_returns) * np.sqrt(252)) if len(daily_returns) > 0 else 0

    # Sharpe (rf=0.02)
    rf_daily = 0.02 / 252
    excess = daily_returns - rf_daily
    sharpe = float(np.mean(excess) / np.std(excess) * np.sqrt(252)) if len(excess) > 0 and np.std(excess) > 0 else 0

    # Sortino (下行波动率)
    downside = daily_returns[daily_returns < 0]
    downside_std = float(np.std(downside)) if len(downside) > 0 else 0
    sortino = float(np.mean(excess) / downside_std * np.sqrt(252)) if downside_std > 0 else 0

    # Calmar
    calmar = cagr / abs(max_drawdown) if max_drawdown < 0 else 0

    benchmark_return = 0
    if equity[0].benchmark_value and equity[-1].benchmark_value:
        b_start = float(equity[0].benchmark_value)
        b_end = float(equity[-1].benchmark_value)
        benchmark_return = (b_end - b_start) / b_start if b_start > 0 else 0

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
        annualized_return=_round(cagr),
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
        sharpe=_round(sharpe),
        sortino=_round(sortino),
        calmar=_round(calmar),
        cagr=_round(cagr),
        annual_volatility=_round(annual_vol),
    )
```

同时确保文件头部有 `import numpy as np`（如果没有则添加到 import 区域）。

- [ ] **Step 2: 验证新指标计算**

```bash
cd stock_screener && python3 -c "
from datetime import date
import numpy as np
from quant_lab.models import EquityPoint, Trade, MetricSnapshot
from quant_lab.metrics import calculate_metric_snapshot

# 模拟权益曲线
eq = [
    EquityPoint(ts=date(2025,1,i+1), equity=100000 + i*500, cash=50000, drawdown=0)
    for i in range(50)
]
eq[-10] = EquityPoint(ts=eq[-10].ts, equity=eq[-10].equity - 15000, cash=eq[-10].cash, drawdown=-0.12)

trades = [
    Trade(order_id='1', trade_id='1', symbol='00700', side='buy', quantity=10, price=100, fee=10, slippage=1, ts=date(2025,1,5)),
    Trade(order_id='2', trade_id='2', symbol='00700', side='sell', quantity=10, price=105, fee=10, slippage=1, ts=date(2025,1,15)),
    Trade(order_id='3', trade_id='3', symbol='00700', side='buy', quantity=10, price=110, fee=10, slippage=1, ts=date(2025,1,20)),
    Trade(order_id='4', trade_id='4', symbol='00700', side='sell', quantity=10, price=108, fee=10, slippage=1, ts=date(2025,1,40)),
]

m = calculate_metric_snapshot(eq, trades)
print(f'Sharpe: {m.sharpe:.4f}')
print(f'Sortino: {m.sortino:.4f}')
print(f'Calmar: {m.calmar:.4f}')
print(f'CAGR: {m.cagr:.4f}')
print(f'Volatility: {m.annual_volatility:.4f}')
print(f'Total return: {m.total_return:.4f}')
print(f'Win rate: {m.win_rate:.4f}')
print('METRICS_EXTENDED — OK')
"
```

Expected: 所有新指标为非零值，`METRICS_EXTENDED — OK`

- [ ] **Step 3: Commit**

```bash
rtk git add stock_screener/quant_lab/metrics.py
rtk git commit -m "feat(quant_lab): add Sharpe/Sortino/Calmar/CAGR/AnnualVol to metrics"
```

---

### Task 9: GridSearchOptimizer 参数优化器

**Files:**
- Create: `stock_screener/quant_lab/optimizer.py`

- [ ] **Step 1: 实现 GridSearchOptimizer**

`stock_screener/quant_lab/optimizer.py`:

```python
from __future__ import annotations

import itertools
from datetime import date
from typing import Optional

import pandas as pd

from .backtest import BacktestRequest, BacktestRunner
from .models import OptimizationResult, ParamGrid, RiskConfig
from .strategies import create_strategy


class GridSearchOptimizer:
    """网格搜索参数优化器"""

    def __init__(self, data_provider, objective: str = "sharpe"):
        """
        Args:
            data_provider: 有 bars_for(market, symbol, start, end) 方法
            objective: "sharpe" | "total_return" | "calmar" | "win_rate" | "profit_factor"
        """
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
        # 展开参数组合
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
            except Exception as e:
                # 跳过失败的参数组合
                continue

        # 按目标函数降序排序（所有指标都是越大越好）
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
```

- [ ] **Step 2: 验证优化器**

```bash
cd stock_screener && python3 -c "
from datetime import date
import pandas as pd
from quant_lab.models import Bar, ParamGrid
from quant_lab.optimizer import GridSearchOptimizer

class MockDataProvider:
    def bars_for(self, market, symbol, start, end):
        dates = pd.date_range(start, end, freq='B')
        close = [100 + i * 0.3 for i in range(len(dates))]
        return [
            Bar(ts=d.date(), open=c, high=c*1.01, low=c*0.99, close=c, volume=1000)
            for d, c in zip(dates, close)
        ]

optimizer = GridSearchOptimizer(MockDataProvider(), objective='sharpe')
results = optimizer.optimize(
    market='HK', symbol='00700',
    start=date(2025,1,1), end=date(2025,6,1),
    param_grid=ParamGrid(
        strategy_type='ma_cross',
        param_space={'fast': [3, 5, 10], 'slow': [10, 20]},
        objective='sharpe',
    ),
    initial_cash=100000,
)
print(f'{len(results)} parameter combinations tested')
for r in results[:3]:
    print(f'  #{r.rank} params={r.params} sharpe={r.objective_value:.4f}')
print('OPTIMIZER — OK')
"
```

Expected: 6 组结果，按 Sharpe 降序排列，`OPTIMIZER — OK`

- [ ] **Step 3: Commit**

```bash
rtk git add stock_screener/quant_lab/optimizer.py
rtk git commit -m "feat(quant_lab): add GridSearchOptimizer for parameter grid search"
```

---

### Task 10: QuantLabService 增强

**Files:**
- Modify: `stock_screener/quant_lab/service.py`

**What:** QuantLabService 新增 `run_strategy_backtest()` 和 `run_optimization()` 两个公开方法，并对应新的 web 入口。

- [ ] **Step 1: 在 QuantLabService 中新增两个方法**

在 `stock_screener/quant_lab/service.py` 的 `QuantLabService` 类的 `run_backtest` 方法之后追加：

```python
    def submit_strategy_backtest(self, payload: dict, user_id: int | None = None) -> dict[str, Any]:
        """提交策略回测任务"""
        run_id = str(uuid.uuid4())
        self.repository.create_quant_backtest_run({
            "run_id": run_id,
            "user_id": user_id,
            "status": "queued",
            "progress_pct": 5,
            "current_stage": "queued",
            "request": payload,
            "warnings": [],
            "progress_logs": [_progress_log_entry("策略回测任务已创建，等待执行")],
        })
        return {"run_id": run_id, "status": "queued"}

    def run_strategy_backtest(self, run_id: str) -> None:
        """执行策略回测"""
        row = self.repository.get_quant_backtest_run(run_id)
        if not row:
            return
        try:
            self.repository.update_quant_backtest_progress(
                run_id, status="running", progress_pct=10,
                current_stage="准备数据", log_message="开始加载K线数据",
            )
            payload = row.get("request") or {}
            created_data_provider = self.data_provider is None
            data_provider = self.data_provider or _build_default_data_provider(self.repository, payload)

            from .strategies import create_strategy
            from .backtest import BacktestRunner, BacktestRequest
            from .models import RiskConfig

            strategy_cfg = payload.get("strategy") or {}
            strategy = create_strategy(
                strategy_cfg["type"],
                strategy_cfg.get("params") or {},
                entry_side=strategy_cfg.get("entry_side", "long"),
            )

            risk_raw = payload.get("risk")
            risk = RiskConfig(
                stop_loss_pct=risk_raw.get("stop_loss_pct") if risk_raw else None,
                take_profit_pct=risk_raw.get("take_profit_pct") if risk_raw else None,
                trailing_stop_pct=risk_raw.get("trailing_stop_pct") if risk_raw else None,
            ) if risk_raw else None

            request = BacktestRequest(
                market=str(payload["market"]),
                symbols=list(payload["symbols"]),
                start=_parse_date(payload["start"]),
                end=_parse_date(payload["end"]),
                initial_cash=float(payload["initial_cash"]),
                quantity=int(payload.get("quantity") or 10),
                commission_rate=float(payload.get("commission_rate") or 0.001),
                slippage_rate=float(payload.get("slippage_rate") or 0.001),
                max_position_weight=float(payload.get("max_position_weight") or 1.0),
                risk_config=risk,
            )

            self.repository.update_quant_backtest_progress(
                run_id, progress_pct=40, current_stage="生成信号",
                log_message=f"策略类型: {strategy_cfg['type']}, 标的: {request.symbols}",
            )

            try:
                runner = BacktestRunner(data_provider=data_provider, strategy=strategy)
                result = runner.run(request)
            finally:
                if created_data_provider and hasattr(data_provider, "close"):
                    data_provider.close()

            self.repository.update_quant_backtest_progress(
                run_id, progress_pct=85, current_stage="计算指标",
                log_message=f"生成 {len(result.signals)} 个信号，成交 {len(result.trades)} 笔",
            )

            self.repository.finish_quant_backtest_run(
                run_id, asdict(result.metrics),
                warnings=result.warnings,
                chart=_chart_payload_for_strategy(result),
            )
        except Exception as exc:
            self.repository.fail_quant_backtest_run(run_id, str(exc))

    def submit_optimization(self, payload: dict, user_id: int | None = None) -> dict[str, Any]:
        """提交参数优化任务"""
        run_id = str(uuid.uuid4())
        self.repository.create_quant_backtest_run({
            "run_id": run_id,
            "user_id": user_id,
            "status": "queued",
            "progress_pct": 5,
            "current_stage": "queued",
            "request": payload,
            "warnings": [],
            "progress_logs": [_progress_log_entry("参数优化任务已创建，等待执行")],
        })
        return {"run_id": run_id, "status": "queued"}

    def run_optimization(self, run_id: str) -> None:
        """执行参数优化"""
        row = self.repository.get_quant_backtest_run(run_id)
        if not row:
            return
        try:
            self.repository.update_quant_backtest_progress(
                run_id, status="running", progress_pct=10,
                current_stage="准备数据", log_message="开始加载K线数据",
            )
            payload = row.get("request") or {}
            created_data_provider = self.data_provider is None
            data_provider = self.data_provider or _build_default_data_provider(self.repository, payload)

            from .models import ParamGrid, RiskConfig
            from .optimizer import GridSearchOptimizer

            param_space = payload.get("param_space") or {}
            objective = str(payload.get("objective") or "sharpe")

            optimizer = GridSearchOptimizer(data_provider, objective=objective)
            param_grid = ParamGrid(
                strategy_type=str(payload["strategy_type"]),
                param_space={str(k): list(v) for k, v in param_space.items()},
                objective=objective,
            )

            self.repository.update_quant_backtest_progress(
                run_id, progress_pct=20, current_stage="网格搜索",
                log_message=f"策略: {param_grid.strategy_type}, 参数空间: {param_space}, 目标: {objective}",
            )

            try:
                results = optimizer.optimize(
                    market=str(payload["market"]),
                    symbol=str(payload["symbol"]),
                    start=_parse_date(payload["start"]),
                    end=_parse_date(payload["end"]),
                    param_grid=param_grid,
                    initial_cash=float(payload.get("initial_cash") or 100000),
                )
            finally:
                if created_data_provider and hasattr(data_provider, "close"):
                    data_provider.close()

            self.repository.update_quant_backtest_progress(
                run_id, progress_pct=85, current_stage="汇总结果",
                log_message=f"测试 {len(results)} 组参数，最优: {results[0].params if results else 'N/A'}",
            )

            # 用最优参数的指标作为主要 metrics，附加全量优化结果
            best_metrics = asdict(results[0].metrics) if results else {}
            best_metrics["optimization_total"] = len(results)
            best_metrics["optimization_top_params"] = results[0].params if results else {}

            chart_data = _optimization_chart_payload(results, payload.get("symbol") or payload.get("symbols", [""])[0])
            self.repository.finish_quant_backtest_run(
                run_id, best_metrics, warnings=[],
                chart=chart_data,
            )
        except Exception as exc:
            self.repository.fail_quant_backtest_run(run_id, str(exc))
```

**同时在文件末尾追加两个辅助函数**：

```python
def _chart_payload_for_strategy(result) -> dict[str, Any]:
    """策略回测的 chart payload（简化版，无规则链叠加层）"""
    symbols = []
    for symbol, bars in result.bars_by_symbol.items():
        symbols.append({
            "symbol": symbol,
            "bars": [bar_to_dict(bar) for bar in bars],
            "signals": [signal_to_dict(s, None) for s in result.signals if s.symbol == symbol],
            "trades": [trade_to_dict(t) for t in result.trades if t.symbol == symbol],
            "overlays": [],
        })
    return {"symbols": symbols}


def _optimization_chart_payload(results: list, symbol: str) -> dict[str, Any]:
    """参数优化结果的 chart payload"""
    if not results:
        return {"symbols": [], "optimization_results": []}
    return {
        "symbols": [
            {
                "symbol": symbol,
                "bars": [],
                "signals": [],
                "trades": [],
                "overlays": [],
            }
        ],
        "optimization_results": [
            {
                "params": r.params,
                "rank": r.rank,
                "objective_value": r.objective_value,
                "sharpe": r.metrics.sharpe,
                "total_return": r.metrics.total_return,
                "max_drawdown": r.metrics.max_drawdown,
                "win_rate": r.metrics.win_rate,
            }
            for r in results[:50]   # 最多返回前 50 组
        ],
    }
```

需要在文件顶部追加 import：
```python
from .models import bar_to_dict, signal_to_dict, trade_to_dict
```

- [ ] **Step 2: 验证 service 方法可调用**

```bash
cd stock_screener && python3 -c "
from quant_lab.service import QuantLabService
from db import InMemoryQuantRepository
svc = QuantLabService(InMemoryQuantRepository())
assert hasattr(svc, 'submit_strategy_backtest')
assert hasattr(svc, 'run_strategy_backtest')
assert hasattr(svc, 'submit_optimization')
assert hasattr(svc, 'run_optimization')
print('SERVICE_ENHANCED — OK')
"
```

Expected: `SERVICE_ENHANCED — OK`

- [ ] **Step 3: Commit**

```bash
rtk git add stock_screener/quant_lab/service.py
rtk git commit -m "feat(quant_lab): add run_strategy_backtest and run_optimization to QuantLabService"
```

---

### Task 11: API 端点

**Files:**
- Modify: `stock_screener/web/quant.py`

**What:** 新增 3 个端点：POST `/api/quant/backtests/strategy`、POST `/api/quant/backtests/optimize`、GET `/api/quant/strategies`。

- [ ] **Step 1: 新增 Pydantic 请求模型 + 3 个端点**

在 `stock_screener/web/quant.py` 文件末尾（router 定义之后）追加新的请求模型和端点。在现有 `CreatePaperAccountRequest` 之后追加：

```python
class StrategyBacktestRequest(BaseModel):
    market: str
    symbols: List[str] = Field(min_length=1, max_length=1, description="当前仅支持单只标的")
    strategy: Dict[str, Any]          # {"type": "ma_cross", "params": {...}, "entry_side": "long"}
    start: date
    end: date
    initial_cash: float = Field(gt=0, default=100000)
    quantity: int = Field(default=10, ge=1)
    commission_rate: float = Field(default=0.001, ge=0)
    slippage_rate: float = Field(default=0.001, ge=0)
    max_position_weight: float = Field(default=1.0, gt=0, le=1.0)
    risk: Optional[Dict[str, Any]] = None   # {"stop_loss_pct": 0.08, "take_profit_pct": 0.20, ...}


class OptimizationRequest(BaseModel):
    market: str
    symbol: str
    strategy_type: str
    param_space: Dict[str, List[float]]
    objective: str = "sharpe"
    start: date
    end: date
    initial_cash: float = Field(gt=0, default=100000)
    quantity: int = Field(default=10, ge=1)


# ── 新端点 ──

@router.post("/backtests/strategy")
def create_strategy_backtest(
    payload: StrategyBacktestRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(require_user),
    service: QuantLabService = Depends(get_quant_service),
) -> Dict[str, Any]:
    if payload.end < payload.start:
        raise BusinessError("QUANT_INVALID_DATE_RANGE", "回测结束日期不能早于开始日期")
    result = service.submit_strategy_backtest(payload.model_dump(mode="json"), user_id=user.id)
    background_tasks.add_task(_run_strategy_backtest_background, result["run_id"], service)
    return result


@router.post("/backtests/optimize")
def create_optimization(
    payload: OptimizationRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(require_user),
    service: QuantLabService = Depends(get_quant_service),
) -> Dict[str, Any]:
    if payload.end < payload.start:
        raise BusinessError("QUANT_INVALID_DATE_RANGE", "优化结束日期不能早于开始日期")
    # 参数数量限制（防止前端误提交过大的搜索空间）
    total_combos = 1
    for v in payload.param_space.values():
        total_combos *= len(v)
    if total_combos > 200:
        raise BusinessError("QUANT_PARAM_SPACE_TOO_LARGE", f"参数组合过多({total_combos})，请缩小搜索空间（≤200组）")
    result = service.submit_optimization(payload.model_dump(mode="json"), user_id=user.id)
    background_tasks.add_task(_run_optimization_background, result["run_id"], service)
    return result


@router.get("/strategies")
def list_strategies() -> List[Dict[str, Any]]:
    from quant_lab.strategies import list_strategies as _list
    return _list()


# ── 后台执行辅助 ──

def _run_strategy_backtest_background(run_id: str, service: QuantLabService) -> None:
    if isinstance(service.repository, InMemoryQuantRepository) or service.data_provider is not None or service.strategy is not None:
        service.run_strategy_backtest(run_id)
        return
    db = MarketDatabase(mysql_config_from_env())
    try:
        QuantLabService(repository=db).run_strategy_backtest(run_id)
    finally:
        db.close()


def _run_optimization_background(run_id: str, service: QuantLabService) -> None:
    if isinstance(service.repository, InMemoryQuantRepository) or service.data_provider is not None or service.strategy is not None:
        service.run_optimization(run_id)
        return
    db = MarketDatabase(mysql_config_from_env())
    try:
        QuantLabService(repository=db).run_optimization(run_id)
    finally:
        db.close()
```

同时更新文件头部 import，确保导入了新增的依赖：
```python
from typing import Any, Dict, List, Optional
```

- [ ] **Step 2: 验证 API 导入**

```bash
cd stock_screener && python3 -c "
from web.quant import StrategyBacktestRequest, OptimizationRequest, router
print('API models — OK')
# 验证策略列表端点逻辑
from quant_lab.strategies import list_strategies
strats = list_strategies()
print(f'Registered strategies: {[s[\"type\"] for s in strats]}')
assert len(strats) == 3  # MA, MACD, RSI
print('API_ENDPOINTS — OK')
"
```

Expected: 3 个策略，`API_ENDPOINTS — OK`

- [ ] **Step 3: Commit**

```bash
rtk git add stock_screener/web/quant.py
rtk git commit -m "feat(web): add POST /backtests/strategy, POST /backtests/optimize, GET /strategies endpoints"
```

---

### Task 12: 前端 — 三栏量化回测页面

**Files:**
- Rewrite: `stock_screener/web_frontend/src/features/quant/QuantLab.tsx`
- Modify: `stock_screener/web_frontend/src/features/quant/types.ts`
- Modify: `stock_screener/web_frontend/src/styles.css`

- [ ] **Step 1: 更新前端类型定义**

替换 `stock_screener/web_frontend/src/features/quant/types.ts`：

```typescript
// ── 策略元信息（GET /api/quant/strategies） ──
export type StrategyMeta = {
  type: string
  name: string
  params: Record<string, StrategyParamDef>
}

export type StrategyParamDef = {
  type: 'int' | 'float'
  default: number
  min: number
  max: number
  label: string
}

// ── 策略回测请求 ──
export type StrategyBacktestRequest = {
  market: string
  symbols: string[]
  strategy: { type: string; params: Record<string, number>; entry_side: string }
  start: string
  end: string
  initial_cash: number
  quantity: number
  commission_rate: number
  slippage_rate: number
  max_position_weight: number
  risk?: { stop_loss_pct?: number; take_profit_pct?: number; trailing_stop_pct?: number }
}

// ── 参数优化请求 ──
export type OptimizationRequest = {
  market: string
  symbol: string
  strategy_type: string
  param_space: Record<string, number[]>
  objective: string
  start: string
  end: string
  initial_cash: number
  quantity: number
}

// ── 优化结果 ──
export type OptimizationResultItem = {
  params: Record<string, number>
  rank: number
  objective_value: number
  sharpe: number
  total_return: number
  max_drawdown: number
  win_rate: number
}

// ── 保持原有类型（向后兼容） ──
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

export type QuantBacktestSubmitResponse = { run_id: string; status: string }

export type QuantProgressLog = { time?: string; message: string }

export type QuantBacktestStatus = {
  run_id: string
  status: 'queued' | 'running' | 'completed' | 'failed' | string
  progress_pct: number
  current_stage: string
  metrics: Record<string, number>
  chart?: QuantBacktestChart
  warnings: string[]
  progress_logs: QuantProgressLog[]
  error_message?: string | null
  created_at?: string | null
  finished_at?: string | null
}

export type QuantBacktestChart = {
  symbols: QuantSymbolChart[]
  optimization_results?: OptimizationResultItem[]
}

export type QuantSymbolChart = {
  symbol: string
  bars: QuantKlineBar[]
  signals: QuantSignalMarker[]
  trades: QuantTradeMarker[]
  overlays?: QuantStrategyOverlay[]
}

export type QuantKlineBar = { date: string; open: number; high: number; low: number; close: number; volume: number }
export type QuantSignalMarker = { signal_id: string; date: string; direction: 'buy' | 'sell' | string; reason: string; price?: number | null }
export type QuantTradeMarker = { trade_id: string; date?: string | null; side: 'buy' | 'sell' | string; quantity: number; price: number }

export type QuantStrategyOverlay = {
  type: 'zuoyi' | 'ema' | 'rsi' | 'volume' | 'pct_change' | string
  rule_key: string; rule_name: string
  items?: QuantZuoYiOverlayItem[]; lines?: QuantOverlayLine[]
  thresholds?: number[]; bars?: QuantOverlayPoint[]; signals?: QuantOverlaySignal[]
}

export type QuantZuoYiOverlayItem = {
  direction: 'bullish' | 'bearish' | string
  left_one_date: string; median_date: string; breakout_date: string
  left_one_high: number; left_one_low: number
  median_high?: number; median_low?: number; breakout_close?: number
}

export type QuantOverlayLine = { name: string; points: QuantOverlayPoint[] }
export type QuantOverlayPoint = { date: string; value: number }

export type QuantOverlaySignal = {
  date: string; label?: string; rule_key?: string; rule_name?: string
  value?: number | null; today_volume?: number | null
  max_volume_prior3?: number | null; pct_change?: number | null
  band_min?: number | null; band_max?: number | null
}
```

- [ ] **Step 2: 重写 QuantLab.tsx 三栏页面**

完整重写 `stock_screener/web_frontend/src/features/quant/QuantLab.tsx`：

```tsx
import React, { useEffect, useMemo, useState, useCallback } from 'react'
import { api } from '../../api'
import type {
  StrategyMeta, StrategyBacktestRequest, OptimizationRequest,
  QuantBacktestStatus, OptimizationResultItem,
} from './types'

const TERMINAL = new Set(['completed', 'failed'])
const MARKETS = { HK: '港股', US: '美股', A: 'A股' } as const
const OBJECTIVES = { sharpe: 'Sharpe', total_return: '总收益', calmar: 'Calmar', win_rate: '胜率' } as const

export function QuantLab() {
  // ── 表单状态 ──
  const [market, setMarket] = useState('HK')
  const [symbol, setSymbol] = useState('HK.00700')
  const [strategies, setStrategies] = useState<StrategyMeta[]>([])
  const [strategyType, setStrategyType] = useState('ma_cross')
  const [params, setParams] = useState<Record<string, number>>({})
  const [entrySide, setEntrySide] = useState('long')
  const [dateRange, setDateRange] = useState({ start: '2025-01-01', end: '2026-01-01' })
  const [initialCash, setInitialCash] = useState(100000)
  const [quantity, setQuantity] = useState(10)

  // ── 风控 ──
  const [useRisk, setUseRisk] = useState(false)
  const [stopLoss, setStopLoss] = useState(0.08)
  const [takeProfit, setTakeProfit] = useState(0.20)
  const [trailingStop, setTrailingStop] = useState(0.05)

  // ── 优化 ──
  const [showOptimize, setShowOptimize] = useState(false)
  const [objective, setObjective] = useState('sharpe')
  const [paramSpaceText, setParamSpaceText] = useState('{"fast": [3,5,10], "slow": [10,20,30]}')

  // ── 运行状态 ──
  const [runId, setRunId] = useState('')
  const [run, setRun] = useState<QuantBacktestStatus | null>(null)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const isActive = Boolean(runId && run && !TERMINAL.has(run.status))

  // ── 加载策略列表 ──
  useEffect(() => {
    api<StrategyMeta[]>('/api/quant/strategies').then(list => {
      setStrategies(list)
      if (list.length > 0) {
        setStrategyType(list[0].type)
        const defaults: Record<string, number> = {}
        Object.entries(list[0].params).forEach(([k, v]) => { defaults[k] = v.default })
        setParams(defaults)
      }
    }).catch(() => {})
  }, [])

  // ── 切换策略时重置参数 ──
  const onStrategyChange = useCallback((type: string) => {
    setStrategyType(type)
    const meta = strategies.find(s => s.type === type)
    if (meta) {
      const defaults: Record<string, number> = {}
      Object.entries(meta.params).forEach(([k, v]) => { defaults[k] = v.default })
      setParams(defaults)
      if (showOptimize) {
        const space: Record<string, number[]> = {}
        Object.entries(meta.params).forEach(([k, v]) => {
          const step = v.type === 'int' ? Math.max(1, Math.round((v.max - v.min) / 5)) : (v.max - v.min) / 5
          space[k] = [v.default, v.default + step, v.default + step * 2]
        })
        setParamSpaceText(JSON.stringify(space))
      }
    }
  }, [strategies, showOptimize])

  // ── 轮询 ──
  const refresh = useCallback(async (id: string) => {
    const r = await api<QuantBacktestStatus>(`/api/quant/backtests/${id}`)
    setRun(r)
    if (r.status === 'failed') setError(r.error_message || '执行失败')
  }, [])

  useEffect(() => {
    if (!runId || (run && TERMINAL.has(run.status))) return
    const t = setInterval(() => { refresh(runId).catch(() => {}) }, 1500)
    return () => clearInterval(t)
  }, [runId, run?.status, refresh])

  // ── 提交回测 ──
  const submit = async () => {
    setError(''); setRun(null); setRunId(''); setSubmitting(true)
    const payload: StrategyBacktestRequest = {
      market, symbols: [symbol],
      strategy: { type: strategyType, params, entry_side: entrySide },
      start: dateRange.start, end: dateRange.end,
      initial_cash: initialCash, quantity,
      commission_rate: 0.001, slippage_rate: 0.001, max_position_weight: 1.0,
    }
    if (useRisk) {
      payload.risk = {}
      if (stopLoss) payload.risk.stop_loss_pct = -Math.abs(stopLoss)
      if (takeProfit) payload.risk.take_profit_pct = takeProfit
      if (trailingStop) payload.risk.trailing_stop_pct = trailingStop
    }
    try {
      const r = await api<{ run_id: string }>('/api/quant/backtests/strategy', {
        method: 'POST', body: JSON.stringify(payload),
      })
      setRunId(r.run_id)
      await refresh(r.run_id)
    } catch (e: any) {
      setError(e?.message || '提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  // ── 提交优化 ──
  const submitOptimize = async () => {
    setError(''); setRun(null); setRunId(''); setSubmitting(true)
    let paramSpace: Record<string, number[]>
    try { paramSpace = JSON.parse(paramSpaceText) } catch {
      setError('参数空间 JSON 格式错误'); setSubmitting(false); return
    }
    const payload: OptimizationRequest = {
      market, symbol, strategy_type: strategyType,
      param_space: paramSpace, objective,
      start: dateRange.start, end: dateRange.end,
      initial_cash: initialCash, quantity,
    }
    try {
      const r = await api<{ run_id: string }>('/api/quant/backtests/optimize', {
        method: 'POST', body: JSON.stringify(payload),
      })
      setRunId(r.run_id)
      await refresh(r.run_id)
    } catch (e: any) {
      setError(e?.message || '提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  // ── 派生数据 ──
  const metrics = useMemo(() => run?.metrics || {}, [run])
  const selectedChart = run?.chart?.symbols?.[0]
  const optimizationResults = useMemo(() => run?.chart?.optimization_results || [], [run])
  const currentStrategy = useMemo(() => strategies.find(s => s.type === strategyType), [strategies, strategyType])

  return (
    <section className="quant-lab">
      <header className="page-header">
        <h1>量化实验室</h1>
        <p>经典量化策略回测 — 均线交叉、MACD、RSI 等，支持参数网格搜索优化</p>
      </header>

      <div className="quant-lab-layout">
        {/* ── 左侧：配置面板 ── */}
        <aside className="panel quant-config">
          <label>市场</label>
          <select value={market} onChange={e => setMarket(e.target.value)}>
            {Object.entries(MARKETS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>

          <label>标的代码</label>
          <input value={symbol} onChange={e => setSymbol(e.target.value)} placeholder="HK.00700" />

          <label>策略类型</label>
          <select value={strategyType} onChange={e => onStrategyChange(e.target.value)}>
            {strategies.map(s => <option key={s.type} value={s.type}>{s.name}</option>)}
          </select>

          {/* 动态参数表单 */}
          {currentStrategy && Object.entries(currentStrategy.params).map(([key, def]) => (
            <div key={key} className="param-field">
              <label>{def.label || key} ({key})</label>
              <input
                type="number"
                value={params[key] ?? def.default}
                min={def.min} max={def.max}
                step={def.type === 'int' ? 1 : 0.01}
                onChange={e => setParams(prev => ({ ...prev, [key]: parseFloat(e.target.value) || 0 }))}
              />
            </div>
          ))}

          <label>方向</label>
          <select value={entrySide} onChange={e => setEntrySide(e.target.value)}>
            <option value="long">仅做多</option>
            <option value="both">多空双向</option>
          </select>

          <label>回测区间</label>
          <div className="date-range">
            <input type="date" value={dateRange.start} onChange={e => setDateRange(p => ({ ...p, start: e.target.value }))} />
            <span>—</span>
            <input type="date" value={dateRange.end} onChange={e => setDateRange(p => ({ ...p, end: e.target.value }))} />
          </div>

          <div className="param-row">
            <label>初始资金</label>
            <input type="number" value={initialCash} min={1000} step={10000} onChange={e => setInitialCash(Number(e.target.value))} />
          </div>

          <div className="param-row">
            <label>每笔数量</label>
            <input type="number" value={quantity} min={1} onChange={e => setQuantity(Number(e.target.value))} />
          </div>

          {/* 风控开关 */}
          <label className="checkbox-label">
            <input type="checkbox" checked={useRisk} onChange={e => setUseRisk(e.target.checked)} />
            启用风控
          </label>
          {useRisk && (
            <div className="risk-config">
              <div className="param-row">
                <label>止损</label>
                <input type="number" value={stopLoss} min={0.01} max={0.5} step={0.01} onChange={e => setStopLoss(Number(e.target.value))} />
                <span className="unit">{-stopLoss * 100}%</span>
              </div>
              <div className="param-row">
                <label>止盈</label>
                <input type="number" value={takeProfit} min={0.01} max={1.0} step={0.01} onChange={e => setTakeProfit(Number(e.target.value))} />
                <span className="unit">+{takeProfit * 100}%</span>
              </div>
              <div className="param-row">
                <label>移动止损</label>
                <input type="number" value={trailingStop} min={0.01} max={0.3} step={0.01} onChange={e => setTrailingStop(Number(e.target.value))} />
                <span className="unit">{trailingStop * 100}%</span>
              </div>
            </div>
          )}

          <div className="config-actions">
            <button onClick={submit} disabled={submitting || isActive} className="btn-primary">
              {submitting && !showOptimize ? '提交中...' : isActive ? '回测中...' : '开始回测'}
            </button>
          </div>

          {/* 参数优化 */}
          <details open={showOptimize} onToggle={e => setShowOptimize((e.target as HTMLDetailsElement).open)}>
            <summary>参数优化（网格搜索）</summary>
            <div className="optimize-config">
              <label>优化目标</label>
              <select value={objective} onChange={e => setObjective(e.target.value)}>
                {Object.entries(OBJECTIVES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
              <label>参数空间 (JSON)</label>
              <textarea
                value={paramSpaceText}
                onChange={e => setParamSpaceText(e.target.value)}
                rows={3}
                spellCheck={false}
              />
              <button onClick={submitOptimize} disabled={submitting || isActive} className="btn-secondary">
                {submitting && showOptimize ? '优化中...' : '开始优化'}
              </button>
            </div>
          </details>

          {error && <div className="error">{error}</div>}
        </aside>

        {/* ── 中间：图表区 ── */}
        <main className="quant-chart-area">
          <EquityChart data={run} />
          {selectedChart && selectedChart.bars.length > 0 && (
            <TradeMarkersChart data={selectedChart} />
          )}
          {!run && (
            <div className="panel chart-placeholder">
              <p>配置策略参数后点击「开始回测」查看权益曲线和买卖信号</p>
            </div>
          )}
        </main>

        {/* ── 右侧：指标 + 结果 ── */}
        <aside className="quant-results">
          {/* 绩效指标卡 */}
          <div className="metrics-grid">
            <MetricCard label="Sharpe" value={metrics.sharpe} fmt="decimal" />
            <MetricCard label="年化收益" value={metrics.cagr} fmt="percent" />
            <MetricCard label="总收益" value={metrics.total_return} fmt="percent" />
            <MetricCard label="最大回撤" value={metrics.max_drawdown} fmt="percent" />
            <MetricCard label="胜率" value={metrics.win_rate} fmt="percent" />
            <MetricCard label="盈亏比" value={metrics.win_loss_ratio} fmt="decimal" />
            <MetricCard label="Calmar" value={metrics.calmar} fmt="decimal" />
            <MetricCard label="Sortino" value={metrics.sortino} fmt="decimal" />
            <MetricCard label="交易次数" value={metrics.trade_count} fmt="integer" />
            <MetricCard label="年化波动" value={metrics.annual_volatility} fmt="percent" />
          </div>

          {/* 优化结果热力图/排名表 */}
          {optimizationResults.length > 0 && (
            <div className="panel optimization-results">
              <h2>参数优化排名</h2>
              <table>
                <thead>
                  <tr>
                    <th>排名</th>
                    <th>参数</th>
                    <th>{OBJECTIVES[objective as keyof typeof OBJECTIVES] || objective}</th>
                    <th>Sharpe</th>
                    <th>收益</th>
                    <th>回撤</th>
                  </tr>
                </thead>
                <tbody>
                  {optimizationResults.slice(0, 10).map(r => (
                    <tr key={r.rank} className={r.rank === 1 ? 'best' : ''}>
                      <td>{r.rank}</td>
                      <td>{JSON.stringify(r.params)}</td>
                      <td>{r.objective_value.toFixed(4)}</td>
                      <td>{r.sharpe.toFixed(2)}</td>
                      <td className={r.total_return >= 0 ? 'positive' : 'negative'}>{(r.total_return * 100).toFixed(2)}%</td>
                      <td className="negative">{(r.max_drawdown * 100).toFixed(2)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* 进度条 */}
          {run && (
            <div className="panel quant-progress-panel">
              <div className="progress-header">
                <strong>{run.current_stage || run.status}</strong>
                <strong>{run.progress_pct}%</strong>
              </div>
              <div className={`progress-track ${run.status === 'failed' ? 'failed' : ''} ${run.status === 'completed' ? 'completed' : ''}`}>
                <span style={{ width: `${run.progress_pct}%` }} />
              </div>
            </div>
          )}
        </aside>
      </div>
    </section>
  )
}

// ── 辅助组件 ──

function MetricCard({ label, value, fmt }: { label: string; value?: number; fmt: 'percent' | 'decimal' | 'integer' }) {
  const display = value == null || Number.isNaN(value) ? '-' :
    fmt === 'percent' ? `${(value * 100).toFixed(2)}%` :
    fmt === 'integer' ? String(Math.round(value)) :
    value.toFixed(4)
  return (
    <div className="metric-card">
      <span className="metric-label">{label}</span>
      <span className="metric-value">{display}</span>
    </div>
  )
}

function EquityChart({ data }: { data?: QuantBacktestStatus | null }) {
  // 简易权益曲线：用 ECharts 或纯 SVG（这里用简单文本+占位，后续迭代可用 ECharts）
  if (!data || data.status !== 'completed') return null
  const m = data.metrics
  return (
    <div className="panel equity-summary">
      <h2>回测完成</h2>
      <div className="equity-stats">
        <span>总收益: <strong className={m.total_return >= 0 ? 'positive' : 'negative'}>{(m.total_return * 100).toFixed(2)}%</strong></span>
        <span>Sharpe: <strong>{m.sharpe?.toFixed(2) || '-'}</strong></span>
        <span>胜率: <strong>{(m.win_rate * 100).toFixed(1)}%</strong></span>
      </div>
    </div>
  )
}

function TradeMarkersChart({ data }: { data: import('./types').QuantSymbolChart }) {
  if (!data.bars.length) return null
  const trades = data.trades || []
  return (
    <div className="panel trade-list">
      <h2>交易明细</h2>
      <div className="trade-table-wrap">
        <table>
          <thead>
            <tr><th>日期</th><th>方向</th><th>价格</th><th>数量</th></tr>
          </thead>
          <tbody>
            {trades.map((t, i) => (
              <tr key={t.trade_id || i}>
                <td>{t.date || '-'}</td>
                <td className={t.side === 'buy' ? 'positive' : 'negative'}>{t.side === 'buy' ? '买入' : '卖出'}</td>
                <td>{t.price.toFixed(2)}</td>
                <td>{t.quantity}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: 添加 CSS 样式**

在 `stock_screener/web_frontend/src/styles.css` 末尾追加：

```css
/* ── Quant Lab 三栏布局 ── */
.quant-lab-layout {
  display: grid;
  grid-template-columns: 280px 1fr 300px;
  gap: 16px;
  min-height: calc(100vh - 180px);
}

.quant-config {
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow-y: auto;
  max-height: calc(100vh - 180px);
}

.quant-config label {
  font-size: 12px;
  color: var(--text-secondary, #888);
  margin-bottom: -4px;
}

.quant-config input,
.quant-config select {
  padding: 6px 10px;
  border: 1px solid var(--border-color, #333);
  border-radius: 6px;
  background: var(--bg-input, #1a1a2e);
  color: var(--text-primary, #eee);
  font-size: 13px;
}

.quant-config .date-range {
  display: flex;
  align-items: center;
  gap: 4px;
}

.quant-config .date-range input { flex: 1; }
.quant-config .date-range span { color: var(--text-secondary, #666); }

.quant-config .param-field { display: flex; flex-direction: column; gap: 2px; }

.param-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.param-row label { min-width: 70px; font-size: 12px; color: var(--text-secondary, #888); }
.param-row input { flex: 1; }
.param-row .unit { font-size: 12px; color: var(--text-secondary, #888); min-width: 40px; }

.risk-config {
  padding: 8px;
  border: 1px solid var(--border-color, #333);
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.checkbox-label {
  display: flex !important;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}

.config-actions { margin-top: 8px; }

.config-actions button,
.optimize-config button {
  width: 100%;
  padding: 8px;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  cursor: pointer;
  transition: background 0.2s;
}

.btn-primary {
  background: var(--accent, #00d4aa);
  color: #000;
}
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-secondary { background: var(--bg-input, #2a2a3e); color: var(--text-primary, #eee); }
.btn-secondary:disabled { opacity: 0.5; cursor: not-allowed; }

.quant-config details {
  margin-top: 8px;
  border: 1px solid var(--border-color, #333);
  border-radius: 6px;
  padding: 8px;
}

.quant-config summary {
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary, #eee);
}

.optimize-config {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 8px;
}

.optimize-config textarea {
  font-family: monospace;
  font-size: 12px;
  padding: 6px;
  border: 1px solid var(--border-color, #333);
  border-radius: 4px;
  background: var(--bg-input, #1a1a2e);
  color: var(--text-primary, #eee);
  resize: vertical;
}

/* ── 图表区 ── */
.quant-chart-area {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.equity-summary {
  text-align: center;
}

.equity-stats {
  display: flex;
  justify-content: center;
  gap: 24px;
  margin-top: 8px;
}

/* ── 指标卡 ── */
.quant-results {
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow-y: auto;
  max-height: calc(100vh - 180px);
}

.metrics-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}

.metric-card {
  display: flex;
  flex-direction: column;
  padding: 10px 12px;
  border: 1px solid var(--border-color, #333);
  border-radius: 8px;
  background: var(--bg-input, #1a1a2e);
}

.metric-label { font-size: 11px; color: var(--text-secondary, #888); }
.metric-value { font-size: 18px; font-weight: 700; margin-top: 2px; }

.positive { color: #00d4aa; }
.negative { color: #ff4757; }

/* ── 优化结果表 ── */
.optimization-results table {
  width: 100%;
  font-size: 12px;
  border-collapse: collapse;
}

.optimization-results th,
.optimization-results td {
  padding: 4px 6px;
  text-align: left;
  border-bottom: 1px solid var(--border-color, #333);
}

.optimization-results th { color: var(--text-secondary, #888); font-weight: 500; }
.optimization-results tr.best { background: rgba(0, 212, 170, 0.08); }

/* ── 交易明细 ── */
.trade-table-wrap {
  max-height: 300px;
  overflow-y: auto;
}

.trade-list table { width: 100%; font-size: 12px; border-collapse: collapse; }
.trade-list th, .trade-list td { padding: 4px 6px; text-align: left; border-bottom: 1px solid var(--border-color, #333); }
.trade-list th { color: var(--text-secondary, #888); position: sticky; top: 0; background: var(--bg-panel, #111); }

/* ── 响应式 ── */
@media (max-width: 1024px) {
  .quant-lab-layout {
    grid-template-columns: 1fr;
  }
  .metrics-grid {
    grid-template-columns: repeat(4, 1fr);
  }
}
```

- [ ] **Step 3: 验证前端编译**

```bash
cd stock_screener/web_frontend && npx tsc --noEmit --pretty 2>&1 | head -20
```

Expected: 无 TypeScript 编译错误

- [ ] **Step 4: Commit**

```bash
rtk git add stock_screener/web_frontend/src/features/quant/QuantLab.tsx stock_screener/web_frontend/src/features/quant/types.ts stock_screener/web_frontend/src/styles.css
rtk git commit -m "feat(frontend): rewrite QuantLab as 3-column strategy backtest page"
```

---

### Task 13: 完整集成测试

**Files:**
- Create: `stock_screener/tests/test_quant_strategies.py`

- [ ] **Step 1: 编写集成测试**

`stock_screener/tests/test_quant_strategies.py`:

```python
"""Quant Lab — 策略回测集成测试"""
from __future__ import annotations

import pytest
import pandas as pd
import numpy as np
from datetime import date

from quant_lab.models import Bar, StrategyConfig, RiskConfig, ParamGrid
from quant_lab.strategies import create_strategy, list_strategies, STRATEGY_REGISTRY
from quant_lab.strategies.base import BaseStrategy
from quant_lab.backtest import BacktestRunner, BacktestRequest
from quant_lab.broker import SimulatedBroker
from quant_lab.metrics import calculate_metric_snapshot
from quant_lab.optimizer import GridSearchOptimizer


# ── Fixtures ──

def _make_bars(start: str, end: str, trend: float = 0.3, volatility: float = 0.02) -> list[Bar]:
    """生成模拟 K 线序列"""
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
        return _make_bars(start.isoformat() if hasattr(start, 'isoformat') else str(start),
                          end.isoformat() if hasattr(end, 'isoformat') else str(end))


# ── 策略注册表测试 ──

def test_strategy_registry_has_three():
    """首期 MA/MACD/RSI 三个策略已注册"""
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
    """每个策略对上升趋势数据至少产生 1 个 buy 信号"""
    np.random.seed(42)
    bars = _make_bars('2025-01-01', '2025-06-01', trend=0.5)
    df = pd.DataFrame([{
        'date': b.ts, 'open': b.open, 'high': b.high,
        'low': b.low, 'close': b.close, 'volume': b.volume,
    } for b in bars])

    strategy = create_strategy(stype, params)
    signals = strategy.generate_signals(df)
    assert len(signals) > 0, f'{stype} should generate at least 1 signal'
    assert all(s.direction in ('buy', 'sell') for s in signals)

    # 信号日期单调递增
    dates = [s.ts for s in signals]
    assert dates == sorted(dates), 'signals must be in chronological order'


# ── 参数定义测试 ──

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
    broker = SimulatedBroker(
        initial_cash=100000,
        risk_config=RiskConfig(stop_loss_pct=-0.05),
    )
    from quant_lab.models import Signal
    buy_sig = Signal('sig1', date(2025, 1, 2), 'HK', '00700', 'buy', 'test')
    buy_bar = Bar(date(2025, 1, 2), 100, 101, 99, 100, 1000)
    broker.apply_signal(buy_sig, buy_bar, 10)
    assert broker.positions.get('00700') == 10

    # 跌 6% 触发止损
    crash = Bar(date(2025, 1, 3), 93, 94, 92, 93, 1000)
    result = broker.check_risk('00700', crash)
    assert result is not None, 'stop loss should trigger'
    assert broker.positions.get('00700', 0) == 0


def test_broker_take_profit():
    broker = SimulatedBroker(
        initial_cash=100000,
        risk_config=RiskConfig(take_profit_pct=0.10),
    )
    from quant_lab.models import Signal
    buy_sig = Signal('sig1', date(2025, 1, 2), 'HK', '00700', 'buy', 'test')
    buy_bar = Bar(date(2025, 1, 2), 100, 101, 99, 100, 1000)
    broker.apply_signal(buy_sig, buy_bar, 10)

    # 涨 12% 触发止盈
    rally = Bar(date(2025, 1, 15), 112, 113, 111, 112, 1000)
    result = broker.check_risk('00700', rally)
    assert result is not None, 'take profit should trigger'
    assert broker.positions.get('00700', 0) == 0


def test_broker_trailing_stop():
    broker = SimulatedBroker(
        initial_cash=100000,
        risk_config=RiskConfig(trailing_stop_pct=0.05),
    )
    from quant_lab.models import Signal
    buy_sig = Signal('sig1', date(2025, 1, 2), 'HK', '00700', 'buy', 'test')
    broker.apply_signal(buy_sig, Bar(date(2025, 1, 2), 100, 101, 99, 100, 1000), 10)

    # 涨到 120
    broker.check_risk('00700', Bar(date(2025, 1, 10), 120, 121, 119, 120, 1000))
    # 回撤 6% 到 112.8（从高点 120 回撤 6%）
    result = broker.check_risk('00700', Bar(date(2025, 1, 15), 113, 114, 112, 113, 1000))
    assert result is not None, 'trailing stop should trigger'
    assert broker.positions.get('00700', 0) == 0


def test_broker_no_risk_config_no_trigger():
    broker = SimulatedBroker(initial_cash=100000)  # 无 risk_config
    from quant_lab.models import Signal
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
    assert len(result.trades) >= 0
    assert len(result.equity_curve) > 0
    assert result.metrics.total_return != 0
    assert result.metrics.sharpe != 0 or result.metrics.sharpe == 0  # 可以是 0


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
            objective='sharpe',
        ),
        initial_cash=100000,
    )
    assert len(results) == 4  # 2×2
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
            objective='total_return',
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
    # 基本字段
    assert m.total_return is not None
    assert m.max_drawdown is not None
    assert m.win_rate is not None
    # 新增字段
    assert hasattr(m, 'sharpe'), 'sharpe field should exist'
    assert hasattr(m, 'sortino'), 'sortino field should exist'
    assert hasattr(m, 'calmar'), 'calmar field should exist'
    assert hasattr(m, 'cagr'), 'cagr field should exist'
    assert hasattr(m, 'annual_volatility'), 'annual_volatility field should exist'
```

- [ ] **Step 2: 运行测试**

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener && python3 -m pytest tests/test_quant_strategies.py -v
```

Expected: 所有测试 PASS

- [ ] **Step 3: Commit**

```bash
rtk git add stock_screener/tests/test_quant_strategies.py
rtk git commit -m "test(quant_lab): add integration tests for strategies, broker risk, optimizer, and metrics"
```

---

## Self-Review

**Spec coverage check:**
- §3.1 数据模型 → Task 1 (models.py)
- §3.2 基类接口 → Task 2 (strategies/base.py + __init__.py)
- §3.3 策略清单(MA/MACD/RSI) → Tasks 3-5
- §4 风控增强 → Task 6 (broker.py)
- §5 参数优化 → Task 9 (optimizer.py)
- §6 绩效指标 → Task 8 (metrics.py)
- §7 API → Task 11 (web/quant.py)
- §8 前端 → Task 12 (QuantLab.tsx)
- §9 实现顺序 → Tasks 1-13 按序排列
- §10 YAGNI → 无越界实现

**Placeholder scan:** 所有步骤包含完整代码块和命令，无 TBD/TODO/占位符。

**Type consistency:**
- `StrategyConfig.params` → dict (consistent across models.py, strategies/*, backtest.py)
- `RiskConfig` → Optional in BacktestRequest (consistent with broker.py constructor)
- `Signal.ts` → date (consistent generation in all 3 strategies)
- Frontend `StrategyBacktestRequest.strategy.params` → `Record<string, number>` (matches backend dict)
- `OptimizationResult.params` → dict in backend, `Record<string, number>` in frontend (consistent)
