# Quant Lab — 经典量化策略回测框架设计

**日期**: 2026-06-16  
**状态**: 已确认  
**范围**: `stock_screener/quant_lab/` — 新增经典策略库 + 参数优化 + 风控增强 + 前端独立回测页面

---

## 一、背景

项目已有量化回测基础框架 `quant_lab/`（BacktestRunner + SimulatedBroker + CachedKlineDataProvider），但策略仅支持「规则链通过→买入，固定持有N天→卖出」。本次增强新增独立于规则链的经典量化策略体系。

**已有基础设施（不变）**：
- K 线数据链路：`DB缓存 > Futu OpenD > YFinance > AKShare`，天然支持港股/美股/A 股
- 回测引擎 + 模拟券商 + 绩效指标
- DB 持久化（`quant_backtest_runs` 表）+ 异步执行
- FastAPI `/api/quant/backtests` + 前端 `QuantLab.tsx`

---

## 二、架构总览

```
quant_lab/
├── strategies/              # 🆕 经典策略库
│   ├── __init__.py
│   ├── base.py              # BaseStrategy 抽象基类
│   ├── ma_cross.py          # 双均线金叉死叉
│   ├── macd.py              # MACD 金叉死叉
│   ├── rsi.py               # RSI 超买超卖
│   ├── bollinger.py         # 布林带突破
│   ├── momentum.py          # 动量/反转策略
│   └── turtle.py            # 海龟交易法则
├── optimizer.py             # 🆕 参数网格搜索 + 结果排名
├── backtest.py              # 🔧 BacktestRunner 支持 Strategy 对象
├── broker.py                # 🔧 止损/止盈/移动止损
├── metrics.py               # 🔧 Sharpe/Sortino/Calmar
├── data_provider.py         # ✅ 不变
├── models.py                # 🔧 新增 StrategyConfig/ParamGrid/RiskConfig
├── service.py               # 🔧 新增 run_strategy_backtest / run_optimization
├── rule_chain_adapter.py    # ✅ 不变（规则链策略保留）
└── ...
```

**核心原则**：策略层与引擎层解耦。策略只管「给定 K 线序列，产生买卖信号」；资金管理、滑点、佣金由 `SimulatedBroker` 统一处理。

---

## 三、策略层

### 3.1 数据模型

```python
@dataclass(frozen=True)
class StrategyConfig:
    strategy_type: str   # "ma_cross" | "macd" | "rsi" | "bollinger" | "momentum" | "turtle"
    params: dict         # {"fast": 5, "slow": 20}
    entry_side: str = "both"  # "long" | "short" | "both"

@dataclass(frozen=True)
class ParamGrid:
    strategy_type: str
    param_space: dict    # {"fast": [5,10,20], "slow": [20,30,60]}
    objective: str = "sharpe"
```

### 3.2 基类接口

```python
class BaseStrategy(ABC):
    def __init__(self, config: StrategyConfig): ...

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> list[Signal]:
        """输入标准化 K 线 DataFrame，输出买卖信号序列"""
        ...

    @staticmethod
    @abstractmethod
    def name() -> str:
        """策略中文名"""
        ...

    @staticmethod
    @abstractmethod
    def param_definitions() -> dict:
        """参数定义，供前端动态渲染表单"""
        ...
```

**关键约定**：一套参数→一次 `generate_signals()`→完整信号序列（纯函数，无状态）。

### 3.3 内置策略清单（6 个）

| 策略 | 类型 | 参数 | 信号逻辑 |
|------|------|------|----------|
| `ma_cross` 双均线交叉 | 趋势跟踪 | `fast`, `slow` | 快线上穿慢线→买；下穿→卖 |
| `macd` MACD | 趋势跟踪 | `fast`, `slow`, `signal` | DIF上穿DEA→买；下穿→卖 |
| `rsi` RSI | 均值回归 | `period`, `oversold`, `overbought` | RSI＜超卖→买；＞超买→卖 |
| `bollinger` 布林带 | 均值回归 | `period`, `std_dev` | 破下轨→买；破上轨→卖 |
| `momentum` 动量 | 趋势跟踪 | `lookback` | 过去N日涨幅＞阈值→买 |
| `turtle` 海龟 | 突破 | `entry_period`, `exit_period`, `atr_period` | 突破N日高→买；跌破M日低→卖 |

**交付分批**：首期实现 MA/MACD/RSI，后续按需扩展。

---

## 四、风控增强（SimulatedBroker）

新增 `RiskConfig`：

```python
@dataclass(frozen=True)
class RiskConfig:
    stop_loss_pct: float | None = None       # 固定止损，如 -0.08
    take_profit_pct: float | None = None     # 固定止盈，如 +0.20
    trailing_stop_pct: float | None = None   # 移动止损，回撤＞X% 出场
```

**逐日检查逻辑**（在信号处理之外，每日收盘后触发）：

```
对每个持仓：
  1. 盈亏 = (close - avg_cost) / avg_cost
  2. if take_profit_pct and 盈亏 >= take_profit_pct → 止盈
  3. if stop_loss_pct   and 盈亏 <= stop_loss_pct   → 止损
  4. if trailing_stop_pct:
       high_water = max(high_water, close)
       if (high_water - close) / high_water >= trailing_stop_pct → 移动止损
```

- 风控出场生成的 `Trade.reason` 字段标记为 `"stop_loss"` / `"take_profit"` / `"trailing_stop"`
- 风控出场优先级高于信号卖出
- `RiskConfig` 可选——不传则沿用纯信号驱动

---

## 五、参数优化

### 5.1 优化器

```python
class GridSearchOptimizer:
    def __init__(self, data_provider, objective: str = "sharpe"): ...

    def optimize(
        self, market, symbol, start, end, param_grid: ParamGrid,
        initial_cash: float = 100_000,
    ) -> list[OptimizationResult]:
        """遍历参数组合→每组回测→按目标排序→返回全量结果"""
```

**流程**：笛卡尔积展开→逐组回测→按 objective 排序→输出排名+权益曲线

### 5.2 前端展示

- 2D 参数热力图（如 fast×slow），颜色表示目标函数值
- 最佳参数高亮 + 完整绩效指标卡
- 一键「应用最优参数」覆盖当前配置

---

## 六、绩效指标扩展

在现有 `MetricSnapshot` 上新增：

| 指标 | 字段 | 说明 |
|------|------|------|
| Sharpe Ratio | `sharpe` | 年化 (收益-无风险利率)/波动率，默认 rf=0.02 |
| Sortino Ratio | `sortino` | 年化超额收益/下行波动率 |
| Calmar Ratio | `calmar` | 年化收益/最大回撤绝对值 |
| CAGR | `cagr` | 复合年化增长率 |
| 年化波动率 | `annual_volatility` | 日收益率标准差×√252 |
| 最大回撤持续期 | `drawdown_duration` | 峰→谷→恢复（已有，补齐计算） |

---

## 七、API

### 7.1 策略回测

```
POST /api/quant/backtests/strategy
```

入参：
```json
{
  "market": "HK",
  "symbols": ["HK.00700"],
  "strategy": {
    "type": "ma_cross",
    "params": {"fast": 5, "slow": 20},
    "entry_side": "long"
  },
  "start": "2025-01-01",
  "end": "2026-01-01",
  "initial_cash": 100000,
  "commission_rate": 0.001,
  "slippage_rate": 0.001,
  "risk": { "stop_loss_pct": 0.08, "take_profit_pct": 0.20, "trailing_stop_pct": 0.05 }
}
```

返回：`{ "run_id": "uuid", "status": "queued" }`（沿用异步轮询）

### 7.2 参数优化

```
POST /api/quant/backtests/optimize
```

入参：
```json
{
  "market": "HK",
  "symbol": "HK.00700",
  "strategy_type": "ma_cross",
  "param_space": {"fast": [5, 10, 20], "slow": [20, 30, 60]},
  "objective": "sharpe",
  "start": "2025-01-01",
  "end": "2026-01-01",
  "initial_cash": 100000
}
```

返回：`{ "run_id": "uuid", "status": "queued" }`

### 7.3 策略参数定义

```
GET /api/quant/strategies
```

返回：
```json
[
  {
    "type": "ma_cross",
    "name": "双均线交叉",
    "params": {
      "fast": {"type": "int", "default": 5, "min": 2, "max": 120},
      "slow": {"type": "int", "default": 20, "min": 5, "max": 250}
    }
  }
]
```

轮询结果沿用现有 `GET /api/quant/backtests/{run_id}`，`metrics` 扩展新字段。

---

## 八、前端页面

### 8.1 页面路由

`/quant-lab` — 独立量化实验室页面，替换现有 `QuantLab.tsx`。

### 8.2 页面布局（三栏）

```
┌─────────────────────────────────────────────────────────┐
│  量化实验室                                              │
├──────────────┬──────────────────────┬────────────────────┤
│  策略配置     │                      │                    │
│              │                      │                    │
│  策略类型 ▾   │     权益曲线图        │   绩效指标卡        │
│  ┌────────┐  │   (ECharts)          │   Sharpe  1.82     │
│  │MA交叉   │  │                      │   年化收益 23.5%    │
│  │MACD    │  │                      │   最大回撤 -12.3%   │
│  │RSI     │  │                      │   胜率    58.2%     │
│  │布林带   │  │                      │   盈亏比  2.15      │
│  │动量    │  │                      │   ...              │
│  │海龟    │  │                      │                    │
│  └────────┘  │                      │                    │
│              │                      │                    │
│  参数表单     │                      ├────────────────────┤
│  快线 [5]   │                      │                    │
│  慢线 [20]  │                      │   交易明细表         │
│              │                      │   日期 | 方向 | 价格 │
│  风控(可选)   │                      │   ...              │
│  止损 [8%]  │                      │                    │
│  止盈 [20%] │                      │                    │
│              │                      │                    │
│  [开始回测]  │                      │                    │
│  [参数优化]  │                      │                    │
└──────────────┴──────────────────────┴────────────────────┘
```

### 8.3 交互流程

1. 顶部搜索栏：输入股票名称/代码 → 自动补全（复用现有搜索 API）
2. 选择策略类型 → 参数表单动态渲染（基于 `GET /api/quant/strategies`）
3. 可选配置风控参数
4. 点击「开始回测」→ POST 提交 → 轮询进度 → 渲染结果
5. 点击「参数优化」→ 展开参数网格配置 → POST 提交 → 轮询 → 渲染热力图+排名
6. 在热力图中点击任意格 → 切换到该参数组合的完整回测结果

---

## 九、实现顺序

| 阶段 | 文件 | 说明 |
|------|------|------|
| 1 | `models.py` | 新增 StrategyConfig/ParamGrid/RiskConfig |
| 2 | `strategies/base.py` | BaseStrategy 抽象基类 |
| 3 | `strategies/ma_cross.py` | 双均线交叉策略 |
| 4 | `strategies/macd.py` | MACD 策略 |
| 5 | `strategies/rsi.py` | RSI 策略 |
| 6 | `broker.py` | RiskConfig 风控增强 |
| 7 | `backtest.py` | BacktestRunner 支持 Strategy 对象 |
| 8 | `metrics.py` | Sharpe/Sortino/Calmar/CAGR |
| 9 | `optimizer.py` | GridSearchOptimizer |
| 10 | `service.py` | run_strategy_backtest / run_optimization |
| 11 | `web/quant.py` | 3 个新 API 端点 |
| 12 | 前端 `QuantLab.tsx` | 三栏布局独立页面 |
| 13 | 测试 | 策略单元测试 + 优化器测试 + API 集成测试 |

---

## 十、不纳入范围（YAGNI）

- ~~Backtrader/vectorbt 作为底层引擎~~（自建，零新依赖）
- ~~多标的组合回测~~（单只股票，后续扩展）
- ~~实时交易/模拟交易执行~~（仅回测，paper.py 保留但不增强）
- ~~盘中分钟线回测~~（仅日线，分钟线后续扩展）
- ~~策略条件组合（AND/OR 多策略叠加）~~（后续版本）
