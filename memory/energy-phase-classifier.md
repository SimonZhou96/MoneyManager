# 能量相位分类器 (EnergyPhaseClassifier)

## 出发点

传统技术指标（RSI、MACD、布林带）各自孤立地测量价格运动的某一个侧面：超买超卖、趋势方向、波动率。它们提供的是**二值判断**（pass/fail），缺少一个统一的**连续数值框架**来刻画股价运动的完整生命周期。

能量相位分类器借鉴物理学中的**势能（Potential Energy）和动能（Kinetic Energy）**隐喻，将价格运动映射为六种连续状态，提供从"积蓄→释放→持续→衰竭→到顶→崩溃"的全周期视角。

**核心洞察**：
- 传统指标告诉你的**是一个时刻的快照**（"RSI超卖了"），但不说"超卖之后会怎样"
- 能量框架告诉你的是**一个状态的转移概率**（"弹簧压紧了→快要释放了"），天然具有时序预测性
- 平方关系（KE ∝ ret², PE ∝ deviation²）放大极端波动，过滤日常噪音

## 六态状态机

```
         COMPRESS ──PE↓ KE↑ ΔE>0──→  RELEASE ──KE持续 PE稳定──→  TRENDING
          (观望)                        (买入)                      (持有)
            ↑                             │                           │
            │                             │                      KE衰减 PE膨胀
            │                             │                           │
          CRASH ←── KE<0 持续 ──────── REVERSAL ←── KE≈0 PE>80 ── EXHAUSTION
          (回避)                                              (预警)
                                                                │
                                                           KE_decay>0.8
                                                           PE>80 KE≈0
                                                                │
                                                              PEAK
                                                             (卖出)
```

### 各状态定义

| 状态 | 含义 | 物理类比 | 判定条件 | 动作 |
|------|------|---------|---------|------|
| **COMPRESS** | 势能积蓄，低动能 | 被压缩的弹簧 | PE>100 & \|KE\|<4 & 方向一致性<0.3 & 累积动能<0 | 观望，等待释放信号 |
| **RELEASE** | 势能释放→动能转化 | 弹簧回弹的瞬间 | KE>0 & ΔE₅>10 & EPR>0.1 | **买入**（底部反弹/突破确认） |
| **TRENDING** | 动能持续主导 | 匀速运动的物体 | 方向一致性>0.7 & KE>0 & PE<100 & 累积动能>0 | 持有/加仓（慢牛识别） |
| **EXHAUSTION** | 动能衰减，势能回升 | 上抛减速阶段 | 动能衰减>0.5 & PE上升 & KE≠0 | 预警，准备减仓 |
| **PEAK** | 动能归零，高位 | 上抛最高点 | 动能衰减>0.8 & PE>80 & KE≈0 & ΔE₅<−10 | **卖出**（到顶拐点） |
| **CRASH** | 空方动能持续 | 自由落体 | 连续5日负动能 & PE>100 & 累积动能<−20 | 回避，不做多 |

> **看涨信号**：当状态为 **RELEASE** 或 **TRENDING** 时，`satisfied=True`，自动被 `unified_bullish_top20` 的 22 条看涨技术规则发现并参与 Top20 评选。

## 核心指标

| 指标 | 公式 | 含义 |
|------|------|------|
| **KE_signed** | sign(ret%) × (ret%)² | 有向动能。+5%涨幅→KE=+25，−5%跌幅→KE=−25。平方项放大极端波动 |
| **PE_norm** | ((close−MA₂₀)/MA₂₀ × 100)² | 归一化势能。价格偏离均线 10%→PE=100。衡量"偏离均衡"的程度 |
| **KE_decay** | 1 − KE_current / KE_peak₂₀ | 动能衰减率。0=无衰减，0.8=动能仅剩峰值的 20%，1=完全耗尽 |
| **KE_consistency** | count(KE>0)₁₀ / 10 | 方向一致性。0.7=近10日有7日方向为正（趋势稳定）。慢牛识别的关键 |
| **ΔE_5** | Σ(KE_t − KE_{t−1})₅ | 5日能量转化速率。正值=能量加速注入，负值=能量流失。RELEASE 判定核心 |
| **KE_path** | Σ(KE)₁₀ | 10日累积动能。正值=近期净动能正向，负值=空方主导。TRENDING 和 CRASH 的关键判断 |
| **EPR** | \|KE\| / (PE + ε) | 能量配分比。>1=动能主导（趋势中），<0.04=势能主导（压缩中） |

## 默认参数

| 参数 | 默认值 | 含义 |
|------|--------|------|
| `ma_period` | 20 | 移动均线周期（均衡基准） |
| `pe_threshold` | 100 | COMPRESS/CRASH 的 PE 触发阈值（对应约10%偏离） |
| `ke_threshold` | 4.0 | COMPRESS 的 KE 沉寂阈值（对应约2%日波动） |
| `epr_release_threshold` | 0.1 | RELEASE 的动能占比最小阈值 |
| `ke_decay_exhaustion` | 0.5 | EXHAUSTION 触发阈值 |
| `ke_decay_peak` | 0.8 | PEAK 触发阈值 |
| `consistency_window` | 10 | 方向一致性窗口（天） |
| `delta_window` | 5 | 能量转化窗口（天） |
| `lookback_pe_days` | 3 | PE 升降回看天数 |
| `min_rows` | 30 | 最少需要 K 线根数 |

### 状态判定参数（2026-06-17 新增，全部可配置）

| 参数 | 默认值 | 影响状态 | 含义 |
|------|--------|---------|------|
| `crash_neg_streak` | 5 | CRASH | 连续负动能天数阈值 |
| `crash_ke_path` | -20.0 | CRASH | 累积动能阈值 |
| `peak_pe_threshold` | 80.0 | PEAK | PE 阈值（~9%偏离） |
| `peak_ke_silence` | 1.0 | PEAK | 动能沉寂阈值（~1%日波动） |
| `peak_delta_e` | -10.0 | PEAK | 能量流失阈值 |
| `release_delta_e` | 10.0 | RELEASE | 能量加速阈值 |
| `trending_consistency` | 0.7 | TRENDING | 方向一致性阈值 |
| `compress_consistency` | 0.3 | COMPRESS | 方向一致性阈值 |
| `compress_ke_path` | 0.0 | COMPRESS | 累积动能阈值 |

## 市场特定参数预设

由于 KE（动能 ∝ ret²）与日收益率平方成正比，不同市场波动率差异导致 KE 值相差数倍（HK 1%日波动→KE=1，A股 3%日波动→KE=9，9x差异）。因此需要按市场调整判定阈值。

### `MARKET_ENERGY_PARAMS` 字典

| 参数 | HK（低波动） | US（中波动） | A（高波动） | 默认值 |
|------|-------------|-------------|------------|--------|
| `release_delta_e` | **5.0** ↓ | 8.0 ↓ | 12.0 ↑ | 10.0 |
| `trending_consistency` | **0.6** ↓ | 0.65 ↓ | 0.75 ↑ | 0.7 |
| `ke_threshold` | **2.5** ↓ | 4.0 | 5.0 ↑ | 4.0 |
| `crash_ke_path` | **-12.0** ↑ | -20.0 | -25.0 ↓ | -20.0 |
| `crash_neg_streak` | **4** ↓ | 5 | 6 ↑ | 5 |
| `peak_ke_silence` | **0.6** ↓ | 1.0 | 1.2 ↑ | 1.0 |
| `peak_delta_e` | **-6.0** ↑ | -10.0 | -12.0 ↓ | -10.0 |
| `compress_consistency` | 0.35 ↑ | 0.3 | 0.3 | 0.3 |

箭头方向：↑ 更容易触发该状态，↓ 更难触发该状态

### 使用方式

```python
# 1. 通过 EnergyPhaseClassifier(market="HK") 自动加载
from strategizers import EnergyPhaseClassifier
ec = EnergyPhaseClassifier(market="HK")  # HK 低波动预设

# 2. 通过 get_market_energy_params() 获取预设 dict
from strategy import get_market_energy_params
params = get_market_energy_params("HK")  # → {"release_delta_e": 5.0, ...}
result = analyze_energy_phases(df, **params)

# 3. 显式覆盖市场预设
ec = EnergyPhaseClassifier(market="HK", release_delta_e=7.0)  # 覆盖 HK 预设

# 4. 回测脚本对比多组参数
python3 -m tests.backtest_energy_phase --code HK.800000 --market HK --compare
```

## 状态判定优先级

判定按优先级从高到低，避免状态歧义：

```
CRASH > PEAK > EXHAUSTION > RELEASE > TRENDING > COMPRESS > UNKNOWN
```

一旦命中高优先级状态，不再检查低优先级。例如：如果一只股票同时满足 CRASH 和 COMPRESS 的条件，将判定为 CRASH（优先）。

## 实例

### 例 1：底部反弹（COMPRESS → RELEASE）

某股从 100 跌至 80，在 80 附近横盘 10 天：

```
阶段            price   MA20   PE_norm   KE_signed   KE_consistency   状态
────────────────────────────────────────────────────────────────────────
下跌中           85     95      123       −6.25       0.2           —
底部横盘 (day30)  80     93      196        0.01       0.1           COMPRESS
微弱反弹 (day31)  81     92      145       +0.50       0.2           COMPRESS
加速突破 (day32)  84     90       44       +4.50       0.3           RELEASE  ← 买入
加速上升 (day33)  88     89        4       +8.00       0.4           RELEASE
趋势确立 (day35)  92     90        4       +8.00       0.8           TRENDING  ← 持有
```

- Day30: PE=196（偏离均线14%） >> 100 → 弹簧压紧了。KE≈0 → 动能沉寂。→ **COMPRESS**
- Day32: KE 从 0.5 跳到 4.5，ΔE₅ 大幅转正，EPR 突破 0.1 → **RELEASE** 触发买入
- Day35: 方向一致性 0.8，PE 回到 4（接近均线），KE 持续为正 → 转入 **TRENDING**

### 例 2：慢牛（TRENDING）

某股 20 天缓慢上涨 10%，每日涨幅约 0.5%：

```
day  price   MA20   PE_norm   KE_signed   KE_consistency   KE_path   状态
───────────────────────────────────────────────────────────────────────
 5   101.5   100.6    1.7       +0.25       0.8             +1.2       —
10   103.2   101.5    3.4       +0.25       0.9             +2.0       TRENDING
15   105.0   102.8    5.5       +0.25       0.8             +1.8       TRENDING
20   107.0   104.2    8.2       +0.25       0.7             +1.5       TRENDING
```

- 全程 KE_consistency > 0.7（10日中至少7日方向为正）
- PE_norm 始终 < 100（价格在均线附近）
- KE_signed 始终 > 0 → **TRENDING** 持续识别
- 不会像传统动量指标那样因为"减速"而误判，因为 KE_path 始终累积为正

### 例 3：冲顶衰竭（TRENDING → EXHAUSTION → PEAK）

某股从 100 快速拉升至 116 后停滞：

```
day  price   MA20   PE_norm   KE_signed   KE_decay   ΔE_5   EPR   状态
─────────────────────────────────────────────────────────────────────────
 5   110     100     100        +4.50      0.0        +18    0.045  TRENDING
10   113     102     121        +4.50      0.0        +10    0.035  TRENDING
13   115     104     121        +2.00      0.56        −8    0.017  —
14   116     106     100        +0.50      0.89        −12   0.005  EXHAUSTION
15   116.5   108      72        +0.01      0.998       −25   0.000  PEAK ⚠️
16   116.3   109      53        −0.04      1.0         −28   0.001  反转确认
17   114     110      16        −2.00      —           —     0.125  (CRASH)
```

- Day13-14: KE 从 4.5 衰减到 0.5，KE_decay 超过 0.5，PE 仍高位 → **EXHAUSTION** 预警
- Day15: KE_decay=0.998（动能只剩峰值的 0.2%），PE=72（仍在高位），KE≈0 → **PEAK** 触发卖出
- Day16-17: KE 转负，确认反转

**关键**：PEAK 在 "涨不动" 的那一刻就发出卖出信号，而不是等跌了才反应。传统指标（如 RSI 超买）可能在 Day13-14 才预警，而 PEAK 在动能耗尽的精确拐点卖。

### 例 4：持续下跌（CRASH）

某股从 100 持续下跌：

```
day  price   MA20   PE_norm   KE_signed   KE_neg_streak   KE_path   状态
──────────────────────────────────────────────────────────────────────
 8    92      97      26        −4.00       1              −15       —
10    85      95     123        −6.25       3              −22       —
11    83      94     145        −2.25       4              −25       —
12    81      93     170        −2.25       5              −28       CRASH
```

- Day12: 连续5日 KE 为负，PE>100（价格偏离均线>13%），KE_path<−20 → **CRASH**

## 与传统指标的对比

| 维度 | 传统指标 | 能量相位 |
|------|---------|---------|
| 输出 | 二值 pass/fail | 六态连续 + 12个数值指标 |
| 时间维度 | 快照（当前是否超买） | 过程（从哪个状态转移到哪个状态） |
| 极端波动 | 线性测量（RSI 变化 1 点 = 1 点） | 平方放大（5%涨幅=25，1%涨幅=1，25倍差异） |
| 慢牛识别 | 困难（RSI 高位钝化，MACD 信号频繁） | 可靠（KE_consistency + KE_path 双重确认） |
| 拐点预测 | 滞后（等 RSI 背离确认） | 前置（PEAK 在动能归零那一刻触发） |
| 可解释性 | 单一数字 | 物理隐喻：弹簧压缩→释放→运动中→减速→到顶 |

## 在 unified_bullish_top20 中的角色

`energy_phase_bullish` 是 `unified_bullish_top20` 的 **第 22 条**（也是最新加入的）看涨技术规则。它：

1. 作为 `direction=bullish`、`strategy_category=technical` 的规则，被 `bullish_technical_rule_keys()` 自动发现
2. 当状态为 RELEASE 或 TRENDING 时，返回 `satisfied=True`，计入该股票的 `bullish_match_count`
3. 与其他 21 条技术规则平等参与 Top20 排名（按 `total_match_count` 降序）
4. 其 12 个数值指标（KE_signed、PE_norm、KE_decay 等）会写入 DB 的 `filter_details` JSON 列，可在 CSV 报告和 LLM 分析中消费

## DB 注册信息

- **rule_key**: `energy_phase_bullish`
- **rule_name**: 能量相位看涨
- **rule_type**: strategy
- **strategy_category**: technical
- **implementation**: EnergyPhaseClassifier
- **direction**: bullish
- **signal_group**: bullish
- **display_order**: 180
- **description**: 六态物理能量相位框架：COMPRESS(势能积蓄)→RELEASE(势能释放)→TRENDING(动能主导)→EXHAUSTION(动能衰竭)→PEAK(到顶)→CRASH(空方动能)。RELEASE或TRENDING时触发看涨信号
