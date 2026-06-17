# 能量相位买入后1-2天即卖出问题

## 现象

RELEASE（买入信号）触发后，1-2个交易日就不再是 bullish 状态（RELEASE/TRENDING），用户感知为"买入后立刻卖出"。

## 已确认机制（已推导验证）

三个结构性问题叠加导致此行为，**不是参数调优能解决的**。

### 机制一：delta_e 在突破次日必然断崖下跌

RELEASE 的核心条件是 `delta_e > release_delta_e`（5日 KE 变化总和超过阈值）。

大阳线突破日 T：KE 从正常水平跳到峰值，KE_diff(T) 贡献巨大正值→delta_e 突破阈值→**RELEASE 触发**。

次日 T+1：KE 回落到正常水平，KE_diff(T+1) = 小值 − 大峰值 = 巨大负值。这个负值填入 5 日窗口后，delta_e 瞬间转负或接近零→**RELEASE 条件丧失**。

```
T-4  T-3  T-2  T-1   T    T+1   T+2
 KE:  1.0  1.5  2.5  5.0  9.0  0.25  0.04
 diff:    +0.5 +1.0 +2.5 +4.0 -8.75 -0.21

T:   delta_e ≈ 0.5+1.0+2.5+4.0+prev ≈ 8+ → RELEASE 🟢
T+1: delta_e ≈ 1.0+2.5+4.0+(−8.75)+?  ≈ −1  → RELEASE 丧失 🔴
```

**结论**：RELEASE 本质上是一个单日事件——它只在动能加速的当天触发，次日自动消失。这不是 bug，是动量加速检测的固有特性。任何基于"变化率"的指标都有这个问题。

### 机制二：KE_decay 的 40 日滚动峰值被单日大阳线污染

```
ke_peak = ke_signed.rolling(40, min_periods=20).max()
ke_decay = 1 − ke_signed / ke_peak
```

一次 3% 涨幅（KE≈9）设置了一个在未来 40 天内都几乎不可逾越的峰值基准。后续任何 KE < 1.8（对应 < 1.3% 日涨幅）都使 KE_decay > 0.8——即 PEAK 判定条件之一自动满足。

物理隐喻的问题：系统中"最大动能"被单次异常波动永久定义，而不是一个稳定的能量尺度。

### 机制三：PEAK/EXHAUSTION 优先级压倒 RELEASE

```
状态判定优先级：CRASH(1) > PEAK(2) > EXHAUSTION(3) > RELEASE(4) > TRENDING(5)
```

突破次日，KE_decay 已达 0.97（远超 PEAK 阈值 0.8）。只要另外三个条件（PE_norm > 80、|KE| < 1.0、delta_e < -10）中的 delta_e 在大约 T+3~T+4 时随正向 KE_diff 滚出窗口而转负，PEAK 就触发并覆盖一切。

即使 PEAK 不满足，EXHAUSTION（KE_decay > 0.5 + pe_rising + |KE| > 0）在 T+1 几乎必然满足，优先级也高于 RELEASE。

## 优化方向（待评估）

### 方向 A：让 bullish 不限于 RELEASE+TRENDING

最简单：EXHAUSTION 也视为 bullish（或至少不是 sell），降低状态切换的剧烈程度。

- 优点：不改算法核心
- 风险：EXHAUSTION 的定义就是动能衰减+势能扩张，本质上是见顶预警。把它当 bullish 会模糊信号含义

### 方向 B：KE_decay 改用自适应峰值

将 `ke_peak` 从 `rolling.max()` 改为分位数（如 `rolling.quantile(0.9)`），这样单日大阳线不会把峰值基准设到无法逾越的高度。

```
# 当前
ke_peak = ke_signed.rolling(40).max()

# 候选：90分位数峰值
ke_peak = ke_signed.rolling(40).quantile(0.9)
```

- 优点：保留物理隐喻，让"衰减"更合理
- 风险：窗口内的极端值被忽略可能漏掉真正的衰竭信号

### 方向 C：KE_decay 用 EMA 平滑后再比较

对 KE 做 EMA 平滑后再计算衰减率，消除单日 spike 的影响。

```
ke_smooth = ke_signed.ewm(span=5).mean()
ke_peak = ke_smooth.rolling(40).max()
ke_decay = 1 − ke_smooth / ke_peak
```

- 优点：大阳线当天的 KE 平滑值不会立刻成为峰值
- 风险：引入滞后，可能延迟卖出信号

### 方向 D：RELEASE 后加锁定期

RELEASE 触发后，锁定 N 天（如 5-10 天）不进入非 bullish 状态，给突破趋势一个"冷却期"来证明自己。

- 优点：最简单，直接解决"买入次日即卖"的问题
- 风险：硬编码锁定期无视真正的趋势逆转

### 方向 E：增加 TRENDING 的缓冲区

当前 TRENDING 要求 `ke_consistency > 0.7`。突破后的大阳线会拉高 ke_consistency，但大阳线滚出窗口后 consistency 会下降。可以降低 TRENDING 的 consistency 阈值，让趋势更不容易"丢失"。

或者：引入"状态迁移惩罚"——从 bullish 状态退出需要满足更严格的条件（如连续 N 天不满足才退出）。

- 优点：减少状态抖动
- 风险：延迟真正的卖出信号

### 方向 F：重构为多时间尺度评分

不输出二值 bullish/bearish，而是输出连续分数（如 0-100）。买入/卖出由外部决策层根据分数阈值和持仓状态决定，而不是由单日状态突变直接触发。

- 优点：从根本上避免了状态机的问题，更接近实际交易决策
- 风险：改动最大，需要重构输出模型和 unified_bullish_top20 的集成方式

## 建议优化顺序

1. **先做方向 B+C 的组合**（KE_decay 自适应峰值 + EMA 平滑）：改动局限在 `analyze_energy_phases` 内部，不影响外部接口。回测脚本 `tests/backtest_energy_phase.py` 可直接对比效果。

2. **再做方向 E 的状态迁移惩罚**：在策略器层面增加"必须连续 N 天非 bullish 才退出"的缓冲逻辑，改动在 `EnergyPhaseClassifier`。

3. **最后考虑方向 A**：如果以上都不够，再评估将 EXHAUSTION 纳入 bullish 或降低其优先级的合理性。

## 回测验证

使用 `tests/backtest_energy_phase.py`：

```bash
python3 -m tests.backtest_energy_phase --code QQQ --market US --compare --details
```

关注指标：信号持续时间（从 RELEASE 触发到退出 bullish 的天数分布）、10 日/20 日胜率、均收益。
