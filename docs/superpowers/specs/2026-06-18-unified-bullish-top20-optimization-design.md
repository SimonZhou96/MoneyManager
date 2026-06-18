# unified_bullish_top20 优化技术设计

> 版本：v1.0
> 日期：2026-06-18
> 状态：设计已确认，待实施
> 面向：选股规则开发、评分体系重构、报告生成优化

---

## 0. 概述

### 0.1 优化目标

基于现有 `unified_bullish_top20` 链路，将「入场信号」和「持有价值」拆分为两个独立评分体系，新增 6 个宏观持有层规则，重写面向散户的报告模板。

### 0.2 核心原则

```
先通过技术规则找到明确入场信号
→ Top20
→ 再评估潜力、宏观、事件、资金和持有价值
→ 输出买点、持有价值、仓位、周期、风险
```

### 0.3 架构选择

**方案 B：「评分层抽离」** —— 规则引擎保持不动，新建 `scoring/` 模块负责分数计算和聚合。

### 0.4 范围

覆盖 P0（评分拆分、报告骨架、热点修正、去冗、风险提示）、P1（6 个新规则、个股卡片）、P2（数据源接口预留）。不新增外部数据源。

---

## 1. 规则清单变更

### 1.1 移除的规则（3 条）

| 规则 key | 原因 |
|---|---|
| `rsi_overbought` | 空头信号，direction 不是 bullish，从未被 bullish_technical_rule_keys() 选中，实际无用 |
| `daily_drop_6_65` | 空头信号，同上 |
| `rsi_oversold` | 功能合并到 `MomentumStateStrategizer`（EntryScorer 内部消费），不再作为独立规则条目 |

### 1.2 新增的规则（11 条）

**入场评分聚合器（5 条）**—— 不替代现有 41 条技术规则的独立评估，只消费它们的 pass/fail 结果：

| 规则 key | 实现类 | 作用 |
|---|---|---|
| `trend_structure` | `TrendStructureStrategizer` | 趋势结构：EMA 排列、左一方向、MA50/MA200 位置 |
| `momentum_state` | `MomentumStateStrategizer` | 动量状态：RSI、MACD、KDJ、能量相位 |
| `volume_confirmation` | `VolumeConfirmationStrategizer` | 成交确认：放量、量价配合 |
| `breakout_quality` | `BreakoutQualityStrategizer` | 突破质量：ATR 突破、新高、形态有效性 |
| `volatility_risk` | `VolatilityRiskStrategizer` | 波动风险：布林带宽、回撤、日内振幅 |

**持有层新规则（6 条）**：

| 规则 key | 实现类 | 级别 | 数据源 |
|---|---|---|---|
| `credit_risk_regime` | `CreditRiskRegimeStrategizer` | 市场级 | YFinance（HYG/LQD/VIX） |
| `market_breadth_regime` | `MarketBreadthRegimeStrategizer` | 市场级 | Futu OpenD + AKShare |
| `liquidity_nowcast` | `LiquidityNowcastStrategizer` | 市场级 | AKShare + Futu OpenD |
| `earnings_revision_momentum` | `EarningsRevisionMomentumStrategizer` | 个股级 | Tavily + 模型 + AKShare |
| `policy_event_risk` | `PolicyEventRiskStrategizer` | 市场级 | Tavily + 模型 |
| `commodity_shock` | `CommodityShockStrategizer` | 市场级 | YFinance + AKShare |

### 1.3 更新的规则（4 条）

| 规则 key | 变更 |
|---|---|
| `energy_phase_bullish` | `MomentumStateStrategizer` 消费其输出作为动量模块加分项 |
| `volume_spike_prior3` | `VolumeConfirmationStrategizer` 消费 |
| `ema_breakout` | `TrendStructureStrategizer` 消费 |
| `zuoyi_bullish_signal` | `BreakoutQualityStrategizer` 消费（同时也被 TrendStructure 消费） |

### 1.4 全景对比

| | 当前 | 优化后 |
|---|---|---|
| 过滤器 | 5 | 5 |
| 技术规则（独立评估） | 41 | 38 |
| 技术聚合器（评分用） | 0 | 5 |
| 宏观/持有规则 | 5 | 11 |
| **合计** | **51** | **59** |

---

## 2. Scoring 模块设计

### 2.1 目录结构

```
stock_screener/scoring/                    ← 新建
├── __init__.py
├── entry_scorer.py        ← 技术规则 → 5模块 → entry_score
├── holding_scorer.py      ← 宏观/事件/资金 → holding维度 → holding_score
├── market_cache.py        ← 市场级规则预计算 + TTL 缓存
├── models.py              ← 所有 scoring 相关 dataclass
└── constants.py           ← 权重常量、阈值映射表
```

### 2.2 数据模型

#### EntryScoreBreakdown

```python
@dataclass
class EntryScoreBreakdown:
    entry_score: float              # 0-100
    entry_decision: str             # "STRONG_BUY" | "VALID_BUY" | "WEAK_BUY" | "NO_BUY"

    trend_score: float              # 趋势结构    权重 30%
    momentum_score: float           # 动量状态    权重 20%
    volume_score: float             # 成交确认    权重 20%
    breakout_score: float           # 突破质量    权重 20%
    volatility_risk_score: float    # 波动风险    权重 10%

    matched_rules: list[str]
    rule_details: dict[str, dict]
    formula: str
```

#### HoldingScoreBreakdown

```python
@dataclass
class HoldingScoreBreakdown:
    holding_score: float            # 0-100
    holding_decision: str           # "WORTH_HOLDING" | "CAN_HOLD" | "SHORT_TERM" | "LIGHT_POSITION" | "NOT_RECOMMENDED"

    enterprise_score: float         # 企业潜力五模块  权重 35%
    event_score: float              # 事件热点验证    权重 20%
    liquidity_score: float          # 资金与流动性    权重 15%
    macro_credit_score: float       # 宏观与信用环境  权重 15%
    earnings_revision_score: float  # 盈利预期修正    权重 10%
    llm_review_score: float         # 模型复核        权重  5%

    holding_period: str             # "短线" | "中线" | "长线"
    position_suggestion: str        # "正常仓位" | "轻仓试探" | "不宜追高"
    risk_level: str                 # "low" | "medium" | "high"
    main_drivers: list[str]
    main_risks: list[str]
    data_gaps: list[str]
    formula: str
```

#### MarketTemperature

```python
@dataclass
class MarketTemperature:
    market: str
    credit_risk: Optional[CreditRiskResult]
    market_breadth: Optional[MarketBreadthResult]
    liquidity: Optional[LiquidityNowcastResult]
    policy_event: Optional[PolicyEventResult]
    commodity_shock: Optional[CommodityShockResult]
    computed_at: str                # ISO timestamp
    ttl_minutes: int = 60
    
    temperature_summary: str        # "偏强 / 中性 / 偏弱"
    money_making_summary: str       # "好 / 一般 / 差"
    capital_env_summary: str        # "流入 / 中性 / 流出"
    risk_appetite_summary: str      # "高 / 中 / 低"
    hot_clarity_summary: str        # "清晰 / 一般 / 混乱"
```

### 2.3 EntryScorer

**公式**：
```
entry_score = trend_score * 0.30
            + momentum_score * 0.20
            + volume_score * 0.20
            + breakout_score * 0.20
            + volatility_risk_score * 0.10
```

**各模块消费的规则（带权重）**：

| 模块 | 规则（权重） |
|---|---|
| 趋势结构 | `zuoyi_bullish_signal`(×1.5), `ema_breakout`(×1.0), `sma_golden_cross`(×1.0), `ema_golden_cross`(×1.0), `above_ma50`(×0.5), `above_ma200`(×0.5) |
| 动量状态 | `energy_phase_bullish`(×1.5), `macd_bullish_cross`(×1.0), `kdj_bullish_cross`(×0.8), `rsi_bullish_rebound`(×0.8), `daily_rise_4_45`(×0.5) |
| 成交确认 | `volume_spike_prior3`(×1.5), `volume_price_breakout`(×1.0), `volume_ratio_high`(×0.8) |
| 突破质量 | `zuoyi_bullish_signal`(×1.0), `atr_breakout`(×1.0), `bollinger_upper_breakout`(×0.8), `new_high_breakout`(×1.2) |
| 波动风险 | `atr_breakout`(×0.5), `bollinger_bandwidth_high`(×0.5), `max_drawdown_20d`(×0.8), `intraday_amplitude`(×0.3) |

> 注：`max_drawdown_20d` 和 `intraday_amplitude` 需要新增计算指标（在 `strategy.py` 中添加），它们不是现有规则 key，而是直接计算的波动度量。

映射：`module_score = min(100, base_50 + raw_weighted * scale_factor)`
- `base_50`：确保无信号时得中性 50 分
- `scale_factor`：调整使 3-4 条核心规则命中时接近 100 分。精确值在实现时通过回测定标

### 2.4 HoldingScorer

**公式**：
```
holding_score = enterprise_score * 0.35
              + event_score * 0.20
              + liquidity_score * 0.15
              + macro_credit_score * 0.15
              + earnings_revision_score * 0.10
              + llm_review_score * 0.05
```

各维度数据来源：

| 维度 | 数据来源 |
|---|---|
| `enterprise_score` | `EnterprisePotentialAnalysisStrategizer.total_score`，缺失=50 |
| `event_score` | `CompanyEventHotSectorStrategizer` + `CompanyEventHotNewsStrategizer` 综合 |
| `liquidity_score` | `LiquidityNowcastStrategizer`（市场级缓存）+ 个股资金流（Futu OpenD） |
| `macro_credit_score` | `CreditRiskRegimeStrategizer` + `MarketIntelMacroScoreStrategizer` 综合 |
| `earnings_revision_score` | `EarningsRevisionMomentumStrategizer`（个股级），缺失=50 |
| `llm_review_score` | 现有 LLM 分析流程的 reliability + confidence |

### 2.5 决策映射

| entry_score | entry_decision | 散户解释 |
|---:|---|---|
| >= 80 | `STRONG_BUY` | 技术信号较强，已有明确入场结构 |
| 65-80 | `VALID_BUY` | 有买入信号，但仍需控制仓位 |
| 50-65 | `WEAK_BUY` | 信号不够强，适合观察 |
| < 50 | `NO_BUY` | 暂不进入观察池 |

| holding_score | holding_decision | 散户解释 |
|---:|---|---|
| >= 80 | `WORTH_HOLDING` | 买点和基本面/事件/环境较匹配 |
| 70-80 | `CAN_HOLD` | 有一定持有价值，注意跟踪风险 |
| 60-70 | `SHORT_TERM` | 有买点，但不适合拿太久 |
| 50-60 | `LIGHT_POSITION` | 只适合小仓位试探 |
| < 50 | `NOT_RECOMMENDED` | 即使有买点，也不建议恋战 |

---

## 3. 6 个新 Strategizer 实现

### 3.1 CreditRiskRegimeStrategizer

- **类型**：市场级
- **数据源**：YFinance（HK/US: HYG/LQD/JNK/IEF/VIX/DXY; A: AKShare 信用债/Shibor）
- **算法**：纯算法，不调 LLM。基于 ETF 20日收益差、最大回撤、VIX 水平的加减分模型
- **输出**：`CreditRiskResult(score=0-100, level="low"|"elevated"|"high", indicators, reason)`
- **缓存**：MarketCache 日频更新，~5 次 YFinance API 调用

### 3.2 MarketBreadthRegimeStrategizer

- **类型**：市场级
- **数据源**：Futu OpenD（全市场个股日 K）+ AKShare（行业涨跌）
- **算法**：计算上涨家数/下跌家数比、MA50上方比例、MA200上方比例、52周新高/新低数量，加减分模型
- **性能注意**：只计算已缓存的日 K，不做实时拉取
- **输出**：`MarketBreadthResult(score=0-100, metrics={above_ma50_pct, adv_decline_ratio, ...}, explanation)`

### 3.3 LiquidityNowcastStrategizer

- **类型**：市场级
- **数据源**：A股=AKShare（融资融券/北向资金/板块资金/ETF资金）；港股=Futu OpenD + AKShare（南向资金）；美股=YFinance（ETF/VOL）
- **算法**：融资余额趋势、北向/南向资金方向、成交额变化、板块资金流连续性
- **输出**：`LiquidityNowcastResult(score=0-100, fund_flow_direction, metrics, explanation)`

### 3.4 EarningsRevisionMomentumStrategizer

- **类型**：个股级（唯一一个个股级新规则）
- **数据源**：Tavily（业绩预告/财报/券商观点）+ AKShare（财务数据对比）
- **算法**：搜索 + LLM 结构化。正面关键词（上修、超预期、订单增长…）加分，负面关键词（下修、低于预期…）减分
- **输出**：`EarningsRevisionResult(score=0-100, earnings_trend="improving"|"stable"|"deteriorating", evidence=[], risks=[], confidence)`
- **性能注意**：仅在 Top20 上逐只执行（最多 20 次 Tavily + LLM 调用）

### 3.5 PolicyEventRiskStrategizer

- **类型**：市场级
- **数据源**：Tavily + LLM
- **算法**：搜索政策/监管/地缘关键词 + LLM 结构化评分
- **输出**：`PolicyEventResult(score=0-100, direction, related_sectors, risk_events, explanation)`

### 3.6 CommodityShockStrategizer

- **类型**：市场级
- **数据源**：YFinance（WTI 原油、铜、黄金、天然气、白银期货）+ A 股补充 AKShare 煤炭
- **算法**：纯价格驱动。5日涨跌幅 > 5% 视为冲击。单一商品温和波动（5-8%）：中性偏正面（score+5）；多商品同时剧烈波动（>10%）：不确定性升高（score-15）；无显著波动：中性（score=50）。
- **输出**：`CommodityShockResult(score=0-100, shocks={}, explanation)`

### 3.7 注册与调用

- 6 个新规则通过 `RuleRegistry.default()` 注册，`strategy_category="macro"`
- 市场级 5 个规则（credit_risk/market_breadth/liquidity/policy_event/commodity_shock）**不逐只股票调用**，分数从 `MarketCache` 注入 `filter_details`
- 仅 `earnings_revision_momentum` 逐只股票执行

---

## 4. 报告模板重构

### 4.1 新报告骨架

```
0. 重要提示（免责声明）
1. 本期一句话结论
2. 本期市场温度计（5 项指标 + 颜色 + 解释）
3. 本期热点方向（行业 / 主题 / 地域三类）
4. 本期评分怎么看（entry_score + holding_score 解释）
5. 本期 Top20 简表（7 列：排名/股票/入场信号/持有价值/建议动作/一句话理由/主要风险）
6. 精选个股卡片（每只个股的结构化卡片：买点原因/持有价值/主要风险/观察点）
7. 分类型建议（短线/中线/不追高 三组）
8. 风险提示（市场/行业/个股/技术/数据 五层）
9. 数据来源与新鲜度
```

### 4.2 实现方式

新建 `signal_analysis/renderers.py`，实现 `RetailReportRenderer` 类。

**`chain.py` 调用简化**：
```python
renderer = RetailReportRenderer()
report_md = renderer.render(context)
```

**现有方法废弃**：`_render_markdown_report()`、`reporting.py` 中的 `render_multi_stock_report()` 标记 deprecated。

### 4.3 HotSectorClassifier

在 `signal_analysis/hot_sectors.py` 中新增分类器，将原始热点从地域标签映射为三类：

- **行业热点**：半导体、券商、消费电子、工业金属…（40+ 英文板块中文翻译）
- **主题热点**：AI、机器人、算力、新能源、低空经济…
- **地域热点**：仅当地域有政策催化时展示（海南自贸区、粤港澳…）

过滤规则：普通省份名不放入热点板块。

---

## 5. 端到端数据流

### 5.1 新筛选流程

```
1. MarketCache.get_or_compute(market)     ← 市场温度计预计算
2. get_merged_pool_stocks()               ← 股票池加载（不变）
3. 5 filters                              ← 基础过滤（不变）
4. evaluate_bullish_technical_rules()     ← 38条技术规则评估（不变，仅移除3条）
5. EntryScorer.compute(filter_details)    ← EntryScore 计算（新）
6. Top20 按 entry_score 排序              ← 排序逻辑变更
7. evaluate_macro_rules_for_top20()       ← 后置宏观（4条现有 + 注入5条市场级缓存 + 1条个股级）
8. HoldingScorer.compute(...)             ← HoldingScore 计算（新）
9. 写 DB（新增 8 个字段）                  ← DB 扩展
10. CSV 导出 + 报告生成                    ← 新渲染器
```

### 5.2 关键变更

**变更 1：Top20 排序** — `total_match_count` → `entry_score`

**变更 2：市场级规则注入方式** — evaluate_macro_rules_for_top20() 中，5 条市场级规则从 MarketCache 读取并注入，不逐只调用

**变更 3：信号分析链注入 MarketTemperature** — MarketIntelEvidenceStep 中加载 MarketTemperature，传递给后续 LLM 分析步骤

### 5.3 DB 扩展

```sql
ALTER TABLE screening_results ADD COLUMN entry_score DECIMAL(5,2);
ALTER TABLE screening_results ADD COLUMN entry_decision VARCHAR(30);
ALTER TABLE screening_results ADD COLUMN holding_score DECIMAL(5,2);
ALTER TABLE screening_results ADD COLUMN holding_decision VARCHAR(30);
ALTER TABLE screening_results ADD COLUMN holding_period VARCHAR(20);
ALTER TABLE screening_results ADD COLUMN position_suggestion VARCHAR(30);
ALTER TABLE screening_results ADD COLUMN risk_level VARCHAR(20);
ALTER TABLE screening_results ADD COLUMN score_formula TEXT;
```

`final_score` 保留一个版本写入 `holding_score` 的值（向后兼容）。

---

## 6. P2 数据源接口预留

### 6.1 抽象接口

```python
class MacroDataProvider(ABC):
    def get_credit_spread(self, market: str) -> Optional[CreditSpreadData]: ...
    def get_financial_conditions(self) -> Optional[FinancialConditionsData]: ...
    def get_analyst_estimates(self, code: str) -> Optional[AnalystEstimateData]: ...

class FREDProvider(MacroDataProvider):       # P2: FRED API
class TushareProProvider(MacroDataProvider): # P2: A 股财务/公告
```

### 6.2 Provider 工厂

```python
def _get_provider(market: str, data_type: str) -> Optional[MacroDataProvider]:
    if data_type == "credit_risk" and os.getenv("FRED_API_KEY"):
        return FREDProvider()
    if data_type == "earnings" and os.getenv("TUSHARE_TOKEN"):
        return TushareProProvider()
    return None  # P0/P1 走 YFinance/AKShare 代理
```

---

## 7. 迁移计划

### Phase 1: 基础设施（1-2天）

- 新建 `scoring/` 模块骨架
- 新建 `signal_analysis/renderers.py`
- `MarketCache` 框架 + `CreditRiskRegimeStrategizer` 实现
- DB migration（新增 8 字段）
- 更新 `RuleRegistry`（注册 6 个新 Strategizer）
- 新旧代码并行，不删除任何旧逻辑
- Feature flag: `USE_NEW_SCORING=0` 回退旧系统

### Phase 2: 评分上线（2-3天）

- `EntryScorer` + `HoldingScorer` 实现 + 测试
- `screen_service` 切换到新评分
- Top20 排序改为 `entry_score`
- `MarketBreadthRegime` + `LiquidityNowcast` + `CommodityShock` 实现
- 新旧评分对比验证

### Phase 3: 报告上线（2-3天）

- `RetailReportRenderer` 实现
- `HotSectorClassifier` 实现
- 个股卡片渲染
- `PolicyEventRisk` + `EarningsRevisionMomentum` 实现
- `chain.py` 切换到新渲染器
- HK/US/A 三市场验证

### Phase 4: 清理 + P2 预留（1天）

- 标记 deprecated: `aggregate_rule_scores`, `compute_unified_score`, `_render_markdown_report`
- 删除 3 条规则注册（rsi_overbought, daily_drop_6_65, rsi_oversold 独立条目）
- 接入 P2 接口抽象
- 回滚：每个 Phase 独立回滚点，Feature flag 控制

---

## 8. 测试策略

### 8.1 单元测试

| 覆盖内容 | 预估用例 |
|---|---|
| 6 个新 Strategizer 各自评分逻辑 | ~30 |
| `EntryScorer` 5 模块映射 + 权重计算 | ~15 |
| `HoldingScorer` 6 维度聚合 | ~15 |
| `MarketCache` TTL + 缓存命中/过期 | ~8 |
| `HotSectorClassifier` 三分类 | ~10 |
| `RetailReportRenderer` 各 section | ~10 |
| **合计** | **~88** |

### 8.2 集成测试

| 覆盖内容 | 预估用例 |
|---|---|
| `screen_service` 端到端（HK/US/A 三市场） | ~6 |
| 回归：`test_unified_bullish_top20` 规则计数更新 | ~2 |
| 回归：`test_e2e_unified_bullish_top20_scoring` 更新 | ~5 |
| **合计** | **~13** |

### 8.3 验收测试

实现完成后，用以下三只股票做全流程端到端验收：

| 市场 | 代码 | 名称 |
|---|---|---|
| A | SH.600999 | 招商证券 |
| US | US.AXP | 美国运通 |
| HK | HK.00128 | 安宁控股 |

验收标准：
1. 三只股票全部通过筛选（is_passed=True）
2. 每只有完整的 filter_details（技术规则 + 宏观规则）
3. 每只有有效的 entry_score + entry_decision + holding_score + holding_decision
4. 结果成功写入 DB
5. CSV 和 Markdown 报告成功生成
6. 报告中市场温度计、热点方向、个股卡片、风险提示均正确展示

---

## 9. 附录：文件变更清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `scoring/__init__.py` | 新建 | 模块入口 |
| `scoring/models.py` | 新建 | EntryScoreBreakdown 等 dataclass |
| `scoring/constants.py` | 新建 | 权重常量、阈值 |
| `scoring/entry_scorer.py` | 新建 | 5 模块聚合 |
| `scoring/holding_scorer.py` | 新建 | 6 维度聚合 |
| `scoring/market_cache.py` | 新建 | MarketCache + 5 个市场级规则 |
| `signal_analysis/renderers.py` | 新建 | RetailReportRenderer |
| `strategizers.py` | 更新 | 新增 6 个 Strategizer 类 |
| `strategy.py` | 更新 | 新增 market_breadth/liquidity 计算函数 |
| `rule_engine.py` | 更新 | RuleRegistry 注册新规则 + evaluate_macro_rules_for_top20 注入市场级缓存 |
| `db.py` | 更新 | DEFAULT_RULE_METADATA 新增/移除条目 + migration |
| `api/screen_service.py` | 更新 | 集成 MarketCache + EntryScorer + HoldingScorer |
| `signal_analysis/chain.py` | 更新 | 切换到 RetailReportRenderer + 加载 MarketTemperature |
| `signal_analysis/hot_sectors.py` | 更新 | HotSectorClassifier |
| `signal_analysis/models.py` | 更新 | UNIFIED_SCORE_WEIGHTS 标记 deprecated |
| `market_intel/macro_scoring.py` | 更新 | aggregate_rule_scores 标记 deprecated |
| `market_intel/reporting.py` | 更新 | 标记 deprecated |
| `tests/test_entry_scorer.py` | 新建 | EntryScorer 单元测试 |
| `tests/test_holding_scorer.py` | 新建 | HoldingScorer 单元测试 |
| `tests/test_market_cache.py` | 新建 | MarketCache 单元测试 |
| `tests/test_new_strategizers.py` | 新建 | 6 个新 Strategizer 单元测试 |
| `tests/test_e2e_unified_bullish_top20_hk02685.py` | 更新 | 技术规则计数 21→20（移除 rsi_oversold 独立条目后） |
| `tests/test_e2e_unified_bullish_top20_scoring.py` | 更新 | 适配 entry_score / holding_score 双分体系，验证新字段 |
| `tests/test_e2e_baseline_3stocks.py` | 已有 | 验收测试（SH.600999/US.AXP/HK.00128） |
