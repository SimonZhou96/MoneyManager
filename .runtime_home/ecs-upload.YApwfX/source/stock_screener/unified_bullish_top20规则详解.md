# unified_bullish_top20 规则详解

> 最后更新：2026-06-17

## 一、总览

`unified_bullish_top20` 是股票筛选平台的核心规则链，采用**两步分离**架构：

```
全部股票 → 21条技术规则筛选 → Top20（按看涨命中数排序）
                                │
                                ▼
              仅 Top20 执行 4条宏观规则 → 合并评分 → 写入DB
```

**链表达式定义**：`db.py:468-493` → `UNIFIED_BULLISH_TOP20_EXPRESSION`

```json
{
  "and": [
    { "all_enabled": ["market_cap_range", "avg_daily_volume_range", "price_range", "pe_range", "profitability"] },
    { "ref": "zuoyi_bullish_signal" },
    { "any_enabled": ["ema_breakout", "rsi_oversold", "rsi_overbought", "volume_spike_prior3", "daily_rise_4_45"] },
    { "all_enabled": ["macro_factor_analysis", "enterprise_potential_analysis", "company_event_hot_sector_link", "company_event_hot_news_link"] }
  ]
}
```

### 1.1 与市场特定默认链的关系

| 市场 | 默认链 key | 宏观因子门禁 | 宏观规则编排 |
|------|-----------|------------|------------|
| A股 | `zuoyi_with_macro_strict` | `{"ref": "macro_factor_analysis"}` 真门禁 | 4+1条（含 `market_intel_macro_score_link`） |
| HK | `zuoyi_with_macro_enhanced` | `any_enabled` 组内软标签 | 4+1条（含 `market_intel_macro_score_link`） |
| US | `zuoyi_with_macro_enhanced` | 同上 | 同上 |

**关键区别**：`unified_bullish_top20` 的宏观规则是后置执行（仅 Top20），市场默认链的宏观规则是内联执行（所有股票）。`market_intel_macro_score_link` 只在市场默认链中使用，不在 `unified_bullish_top20` 的4条宏观规则中。

---

## 二、核心执行代码路径

### 2.1 入口：`api/screen_service.py`

```
run_screening_task()
├─ 全部股票执行 evaluate_bullish_technical_rules() → 21条技术规则 (line 613)
├─ select_unified_bullish_top_candidates() → 选出 Top20 (line 812)
├─ 注入 signal_analysis_loader 闭包 (line 819-840)
└─ 逐只执行 evaluate_macro_rules_for_top20() → 4条宏观规则 (line 850)
   └─ 合并 filter_details → aggregate_rule_scores() 重算 (line 866)
```

### 2.2 宏观规则执行器：`rule_engine.py:728-761`

```python
def evaluate_macro_rules_for_top20(self, stock, filter_context):
    for rule_key in (
        'macro_factor_analysis',           # 规则1
        'enterprise_potential_analysis',   # 规则2
        'company_event_hot_sector_link',   # 规则3
        'company_event_hot_news_link',     # 规则4
    ):
        metadata = self.metadata_by_key.get(rule_key)
        if metadata is not None and metadata.enabled:
            execution.execute(rule_key)
    return StockFilterResult(...)
```

---

## 三、规则1：`macro_factor_analysis` — 宏观因子采集

### 3.1 基本信息

| 属性 | 值 |
|------|-----|
| 中文名 | 宏观因子采集与分析 |
| 实现类 | `potential_analysis/strategizer.py:43` → `MacroFactorAnalysisStrategizer` |
| 规则类型 | `strategy` |
| 策略分类 | `macro` |
| DB 部署 | `sql/018_macro_factor_analysis.sql`（非 `DEFAULT_RULE_METADATA`） |
| display_order | 235 |

### 3.2 参数（按市场区分）

| 参数 | HK | US | A |
|------|-----|-----|-----|
| `min_factors` | 5 | 3 | 5 |

### 3.3 输入

```
StockInfo.market   → 决定采集哪组宏观指标
FilterContext.cache → 读/写 key: "macro_factor_snapshot:{market}"
```

**重要特性**：同一市场的所有股票共享一份 `MacroSnapshot`，仅第一次调用触发数据拉取，后续命中缓存。

### 3.4 处理流程

```
┌─ 查缓存: macro_factor_snapshot:{market}
│   ├─ 命中 → 直接返回
│   └─ 未命中 ↓
├─ 调用 build_default_macro_provider(market)
│   ├─ HK: akshare(CPI/PMI/M2/LPR/社融) + yfinance(VIX/DXY/美债/恒指/SP500)
│   ├─ US: yfinance(美债/VIX/DXY/SP500)
│   └─ A:  akshare(CPI/PMI/M2/LPR/社融) + yfinance(VIX/DXY)
├─ 封装为 MacroSnapshot dataclass
├─ 写入缓存
└─ 构建 StrategizerOutput
```

### 3.5 输入数据明细（MacroSnapshot 字段）

| 类别 | 字段 | 说明 | 数据源 |
|------|------|------|--------|
| 利率 | `policy_rate` | 政策利率 | akshare |
| 利率 | `ten_year_yield` | 10年期国债收益率 | akshare/yfinance |
| 利率 | `two_year_yield` | 2年期国债收益率 | yfinance |
| 利率 | `yield_spread` | 期限利差 (10Y-2Y) | 计算 |
| 利率 | `lpr_1y` | 1年期 LPR | akshare |
| 利率 | `lpr_5y` | 5年期 LPR | akshare |
| 通胀 | `cpi_yoy` | CPI 同比 | akshare |
| 通胀 | `core_cpi_yoy` | 核心 CPI 同比 | akshare |
| 增长 | `pmi_manufacturing` | 制造业 PMI | akshare |
| 增长 | `pmi_services` | 服务业 PMI | akshare |
| 增长 | `gdp_yoy` | GDP 同比 | akshare |
| 增长 | `industrial_production_yoy` | 工业增加值同比 | akshare |
| 就业 | `unemployment_rate` | 失业率 | akshare |
| 货币 | `m2_yoy` | M2 同比 | akshare |
| 货币 | `social_financing_yoy` | 社融同比 | akshare |
| 货币 | `credit_growth_yoy` | 信贷增速同比 | akshare |
| 汇率/风险 | `dxy` | 美元指数 | yfinance |
| 汇率/风险 | `vix` | 恐慌指数 | yfinance |
| 市场指数 | `hsi` / `hsi_change_pct` | 恒生指数 | yfinance |
| 市场指数 | `sp500` / `sp500_change_pct` | 标普500 | yfinance |
| 市场指数 | `shanghai_composite` / `shanghai_composite_change_pct` | 上证综指 | akshare |

### 3.6 输出

```python
StrategizerOutput(
    name="MacroFactorAnalysisStrategizer",
    satisfied=True/False,
    result="pass" if factor_count >= min_factors else "skip"/"error",
    reason="宏观因子采集成功: 15/20 有效 (75%)",
    details={
        "market": "HK",
        "factor_count": 15,          # 有效因子数
        "total_factors": 20,         # 总因子数
        "coverage_pct": 75.0,        # 覆盖率%
        "min_factors_required": 5,
        "factors": [                 # MacroFactorValue 明细
            {"factor_key": "cpi_yoy", "factor_name": "CPI同比", "value": 2.1, ...},
            ...
        ],
        "data_gaps": [...],          # 缺失数据说明
        "provider_status": {...},    # 数据源状态
        # + 所有 MacroSnapshot 字段值
        "cpi_yoy": 2.1,
        "pmi_manufacturing": 50.2,
        "m2_yoy": 9.8,
        "vix": 18.5,
        ...
    }
)
```

### 3.7 三种结果语义

| result | 条件 | 含义 |
|--------|------|------|
| `pass` | `factor_count >= min_factors` | 宏观数据充足 |
| `skip` | `0 < factor_count < min_factors` | 宏观数据不足但未完全失败 |
| `error` | `factor_count == 0` | 宏观数据完全获取失败 |

---

## 四、规则2：`enterprise_potential_analysis` — 企业潜力分析（五模块）

### 4.1 基本信息

| 属性 | 值 |
|------|-----|
| 中文名 | 企业潜力分析(五模块综合评分) |
| 实现类 | `potential_analysis/strategizer.py:113` → `EnterprisePotentialAnalysisStrategizer` |
| 规则类型 | `strategy` |
| 策略分类 | `macro` |
| DB 部署 | `sql/018_macro_factor_analysis.sql`（非 `DEFAULT_RULE_METADATA`） |
| display_order | 240 |

### 4.2 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `threshold` | 70.0 | 通过阈值（0-100分） |
| `weights` | `{"macro":0.30, "industry":0.25, "company":0.25, "valuation":0.10, "trading":0.10}` | 五模块权重 |

### 4.3 输入

```
StockInfo          → code, name, market
FilterContext.cache →
  ├─ "enterprise_service"             → 预取服务（批量场景）
  ├─ "enterprise:evidence:{code}"     → 缓存的证据包（预取产出）
  ├─ "macro_factor_snapshot:{market}" → 规则1的产出
  └─ "signal_analysis:{code}"         → LLM 分析结果（规则3/4也消费）
```

### 4.4 处理流程

```
┌─ 1. 获取证据包
│   ├─ 优先: enterprise:evidence:{code} 缓存
│   ├─ 其次: enterprise_service.load_evidence()
│   └─ 兜底: build_enterprise_evidence(macro_snapshot + yfinance)
│
├─ 2. _inject_signal_analysis() ← 注入 LLM 分析结果
│   ├─ hot_sector_tags → evidence.industry.hot_sector_tags
│   ├─ hot_sector_mark="重点" → evidence.industry.policy_support="strong"
│   ├─ news_impact → evidence.company.event_impact
│   └─ company_hot_news 非空 → evidence.company.news_validation=True
│
├─ 3. EnterprisePotentialScorer.score()
│   ├─ MacroScorer.score(macro)       → 0~100 宏观分
│   ├─ IndustryScorer.score(industry) → 0~100 行业分
│   ├─ CompanyScorer.score(company)   → 0~100 企业质量分
│   ├─ ValuationScorer.score(val)     → 0~100 估值分
│   └─ TradingScorer.score(trading)   → 0~100 交易行为分
│
└─ 4. 加权求和 → total_score → 判定结果
```

### 4.5 五模块评分详解

#### 宏观模块（权重 30%）— `MacroScorer`

| 条件 | 加减分 | 逻辑 |
|------|--------|------|
| LPR_1y ≤ 3.5% | +10 | 低利率利多 |
| LPR_1y ≤ 4.5% | +5 | 中等利率偏多 |
| 10Y ≤ 3.0% | +10 | 低长端利率 |
| CPI 1.0%~3.0% | +10 | 温和通胀 |
| CPI 0%~1.0% | +5 | 偏低通胀 |
| PMI ≥ 52 | +15 | 强劲扩张 |
| PMI ≥ 50 | +10 | 扩张 |
| PMI ≥ 48 | +5 | 微弱收缩 |
| M2 8%~12% | +10 | 适度宽松 |
| M2 > 12% | +5 | 过度宽松 |
| VIX ≤ 20 | +10 | 低恐慌 |
| VIX ≤ 25 | +5 | 中性 |
| VIX > 30 | -10 | 高恐慌 |
| DXY ≤ 100 | +5 | 弱美元利好新兴 |
| DXY ≥ 105 | -5 | 强美元利空 |
| 无数据 | 50.0 | 中性基准 |

#### 行业模块（权重 25%）— `IndustryScorer`

| 条件 | 加减分 | 逻辑 |
|------|--------|------|
| 有 sector | +5 | 有行业分类 |
| 有 hot_sector_tags | +10 | 热点板块标签 |
| hot_sector_tags ≥ 2 | +5 | 多热点共振 |
| growth_stage = "expansion"/"early_recovery" | +10 | 行业扩张期 |
| growth_stage = "contraction" | -10 | 行业收缩期 |
| policy_support = "strong" | +10 | 政策强支撑 |

#### 企业质量模块（权重 25%）— `CompanyScorer`

| 条件 | 加减分 | 逻辑 |
|------|--------|------|
| ROIC/ROE ≥ 20% | +20 | 极优 |
| ROIC/ROE ≥ 15% | +15 | 优秀 |
| ROIC/ROE ≥ 10% | +10 | 良好 |
| ROIC/ROE ≥ 5% | +5 | 尚可 |
| ROIC/ROE < 5% | -10 | 较差 |
| 毛利率 ≥ 40% | +15 | 高毛利 |
| 毛利率 ≥ 30% | +10 | 中等毛利 |
| 毛利率 ≥ 20% | +5 | 低毛利 |
| 净利率 ≥ 20% | +15 | 高净利 |
| 事件影响="利好"/"偏利好" | +10 | LLM 判断 |
| 事件影响="利空"/"偏利空" | -10 | LLM 判断 |
| 新闻验证=True | +5 | LLM 判断 |

#### 估值模块（权重 10%）— `ValuationScorer`

| 条件 | 加减分 | 逻辑 |
|------|--------|------|
| PE ≤ 12 | +20 | 极度低估 |
| PE ≤ 18 | +15 | 低估 |
| PE ≤ 25 | +10 | 合理偏低 |
| PE ≤ 35 | +5 | 合理 |
| PE > 35 | -10 | 偏高 |
| PB ≤ 1.5 | +10 | 破净/低PB |
| PB ≤ 3.0 | +5 | 合理PB |
| PEG ≤ 1.0 | +15 | 成长合理价 |
| PEG ≤ 2.0 | +10 | |
| PEG ≤ 3.0 | +5 | |
| EV/EBITDA ≤ 10 | +10 | 便宜 |
| EV/EBITDA ≤ 15 | +5 | 合理 |

#### 交易行为模块（权重 10%）— `TradingScorer`

评估市场交易行为（波动率、换手率、资金流向等），权重仅 10%，作为辅助判断。

### 4.6 输出

```python
StrategizerOutput(
    name="EnterprisePotentialAnalysisStrategizer",
    satisfied=True/False,       # total_score >= 70
    result="pass"|"fail"|"error",
    reason="综合评分 78.5/100，五模块(宏观82+行业75+企业80+估值70+交易65)=78.5≥70→BUY",
    details={
        "ticker": "HK.00700",
        "total_score": 78.5,          # ⭐ 五模块加权总分
        "macro_score": 82.0,          # 宏观模块分
        "industry_score": 75.0,       # 行业模块分
        "company_score": 80.0,        # 企业质量模块分
        "valuation_score": 70.0,      # 估值模块分
        "trading_score": 65.0,        # 交易模块分
        "passed": True,
        "threshold": 70.0,
        "confidence_score": 85.0,     # 数据覆盖率
        "decision": "BUY",            # ⭐ BUY / WATCH / SKIP
        "holding_period": "中线",      # 建议持有期
        "main_drivers": [             # 主要驱动因子
            "宏观: PMI扩张+低利率",
            "企业: 高ROIC+强毛利"
        ],
        "main_risks": [...],          # 主要风险
        "causal_chain": [...],        # 因果推理链
        "data_gaps": [...],           # 数据缺口
        "modules_available": ["macro","industry","company","valuation","trading"],
        "modules_missing": [],
        "evidence_digest": "...",     # 证据摘要
        "weights": {"macro":0.30,...},# 实际权重
    }
)
```

### 4.7 决策映射

| total_score 区间 | decision | 含义 |
|-----------------|----------|------|
| ≥ 70 | `BUY` | 建议买入 |
| 50~70 | `WATCH` | 观察等待 |
| < 50 | `SKIP` | 建议回避 |

---

## 五、规则3：`company_event_hot_sector_link` — 公司时事×热点板块共振

### 5.1 基本信息

| 属性 | 值 |
|------|-----|
| 中文名 | 公司时事与热点板块关联 |
| 实现类 | `macro_strategies.py:119` → `CompanyEventHotSectorStrategizer` |
| 规则类型 | `strategy` |
| 策略分类 | `macro` |
| DB 元数据 | `DEFAULT_RULE_METADATA` line 375 |
| display_order | 210 |
| 参数 | 无 |

### 5.2 输入

```
FilterContext.cache →
  └─ "signal_analysis:{code}" → SignalAnalysisResult (LLM产出)
       ├─ company_events        → 公司时事列表
       ├─ hot_sectors           → 热点板块列表
       ├─ matched_hot_sectors   → 匹配的热点板块
       ├─ hot_sector_mark       → 热点标记("重点"/"相关")
       └─ hot_sector_reason     → LLM给定的原因
```

**无外部 API 调用**，纯从缓存读取 LLM 已产出的分析结果。

### 5.3 处理流程

```
1. _load_signal_analysis(stock, context)
   ├─ 查缓存: signal_analysis:{code}
   └─ 未命中 → 调用 signal_analysis_loader(stock) 闭包 → 触发 LLM 分析

2. 检查 analysis.analysis_status == "success" ? 否则 → skip
3. 检查 analysis.company_events 非空? 否则 → skip
4. 检查 hot_sectors 或 matched_hot_sectors 非空? 否则 → skip

5. 判定:
   is_pass = bool(matched_hot_sectors) OR hot_sector_mark in {"重点", "相关"}
```

### 5.4 输出

```python
StrategizerOutput(
    name="CompanyEventHotSectorStrategizer",
    satisfied=True/False,
    result="pass" if is_pass else "fail" / "skip",
    reason="公司时事与热点板块形成共振：人工智能、半导体" if is_pass
          else "未看到公司时事与热点板块形成明确共振",

    details={
        "company_events": ["发布AI芯片", "与OpenAI合作"],
        "hot_sectors": ["人工智能", "半导体", "云计算"],
        "matched_hot_sectors": ["人工智能", "半导体"],  # ⭐ 匹配的热点
        "hot_sector_mark": "重点",                       # ⭐ 重点/相关
        "hot_sector_reason": "公司AI芯片直接受益于AI算力需求爆发",
    }
)
```

### 5.5 三种结果语义

| result | 条件 | 含义 |
|--------|------|------|
| `pass` | 有 matched_hot_sectors 或 mark="重点"/"相关" | 共振确认 |
| `fail` | 有数据但未匹配 | 无共振 |
| `skip` | 缺少 signal_analysis / company_events / hot_sectors | 数据不足 |

---

## 六、规则4：`company_event_hot_news_link` — 公司时事×热点新闻验证

### 6.1 基本信息

| 属性 | 值 |
|------|-----|
| 中文名 | 公司时事与热点新闻关联 |
| 实现类 | `macro_strategies.py:283` → `CompanyEventHotNewsStrategizer` |
| 规则类型 | `strategy` |
| 策略分类 | `macro` |
| DB 元数据 | `DEFAULT_RULE_METADATA` line 377 |
| display_order | 220 |
| 参数 | 无 |

### 6.2 输入

```
FilterContext.cache →
  └─ "signal_analysis:{code}" → SignalAnalysisResult (LLM产出)
       ├─ company_events        → 公司时事列表
       ├─ company_hot_news      → 公司相关热点新闻
       ├─ market_hot_news       → 市场热点新闻
       └─ news_impact           → 新闻影响方向
```

### 6.3 处理流程

```
1. _load_signal_analysis(stock, context)  # 同规则3

2. 检查 analysis.analysis_status == "success" ? 否则 → skip
3. 检查 analysis.company_events 非空? 否则 → skip
4. 检查 company_hot_news 或 market_hot_news 非空? 否则 → skip

5. 判定:
   is_pass = bool(company_hot_news) AND news_impact in {"利好","偏利好","利空","偏利空"}
```

### 6.4 输出

```python
StrategizerOutput(
    name="CompanyEventHotNewsStrategizer",
    satisfied=True/False,
    result="pass" if is_pass else "fail" / "skip",
    reason="公司时事已被热点新闻验证，新闻影响为利好" if is_pass
          else "新闻存在，但影响判断为中性，未形成明确验证",

    details={
        "company_events": ["发布AI芯片"],
        "company_hot_news": [                    # ⭐ 公司相关热点新闻
            "腾讯发布新一代AI推理芯片，性能提升3倍",
        ],
        "market_hot_news": [                     # 市场热点新闻
            "AI算力需求持续爆发，芯片厂商扩产",
        ],
        "news_impact": "利好",                   # ⭐ 利好/偏利好/利空/偏利空
    }
)
```

### 6.5 pass 的必要条件

- `company_hot_news` 列表 **非空**（有公司相关热点新闻）
- `news_impact` **在** `{"利好", "偏利好", "利空", "偏利空"}` 中（有方向性判断）

两个条件**同时满足**才 pass。

---

## 七、规则间依赖关系

```
┌────────────────────────────────────────────────────────────┐
│                      FilterContext 缓存层                    │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  market_intel_service ─────── (由 screen_service 注入)      │
│  macro_score_scorer                                         │
│  enterprise_service ───────── (批量预取场景)                 │
│                                                            │
│  ┌─ macro_factor_snapshot:{market} ─── 规则1写入，规则2消费  │
│  │                                                          │
│  ├─ enterprise:evidence:{code} ───── 预取缓存，规则2消费     │
│  │  ├─ enterprise:company:{code}                            │
│  │  ├─ enterprise:valuation:{code}                          │
│  │  └─ ...                                                  │
│  │                                                          │
│  └─ signal_analysis:{code} ───────── LLM产出，规则2/3/4消费  │
│     ▲                                                       │
│     └─ signal_analysis_loader 闭包（按需触发 LLM）            │
│                                                            │
└────────────────────────────────────────────────────────────┘

执行顺序（4条规则）:

  ┌──────────────┐
  │ 规则1        │  macro_factor_analysis
  │ 宏观因子采集  │  → 按 market 共享缓存，仅首次调数据源
  │              │  → 产出 MacroSnapshot
  └──────┬───────┘
         │  MacroSnapshot 写入缓存
         ▼
  ┌──────────────┐
  │ 规则3        │  company_event_hot_sector_link
  │ 时事×板块    │  → 读取 signal_analysis:{code}
  │              │  → 纯判断，无外部调用
  └──────────────┘

  ┌──────────────┐
  │ 规则4        │  company_event_hot_news_link
  │ 时事×新闻    │  → 读取 signal_analysis:{code}
  │              │  → 纯判断，无外部调用
  └──────────────┘

  ┌──────────────┐
  │ 规则2        │  enterprise_potential_analysis
  │ 五模块评分   │  → 消费 MacroSnapshot + signal_analysis
  │              │  → 消费 enterprise_service 预取数据
  │              │  → 加权求和 → 决策
  └──────────────┘
```

---

## 八、评分聚合

### 8.1 `aggregate_rule_scores()` 逻辑

位置：`market_intel/macro_scoring.py:86-140`

**当前权重配置**：
- `DEFAULT_TECHNICAL_WEIGHT = 0.0`（技术规则不参与最终分数）
- `DEFAULT_MACRO_WEIGHT = 1.0`（宏观规则占 100% 权重）

**对 macro 类型规则的取值优先级**：

```
1. details.total_score   → 规则2（EnterprisePotentialAnalysis）的五模块综合分
2. details.macro_score   → MarketIntelMacroScoreStrategizer 的宏观分
3. 两者都缺失 → 不纳入计算
```

**对 technical 类型规则**：pass = 100, fail = 0，取平均。

**最终公式**：
```
final_score = technical_score × 0.0 + macro_score × 1.0
```

实际上最终分**完全由宏观规则的 `total_score` 决定**（通常是规则2的五模块综合分）。

### 8.2 与报告层的关系

报告层使用 `signal_analysis/models.py` 中的 `UNIFIED_SCORE_WEIGHTS`：
```
技术×0% + 五模块×40% + 事件热点×30% + 资金风险×20% + LLM×10%
```
这是**独立的展示公式**，与 DB 层的 `aggregate_rule_scores` 计算逻辑不同，但在报告 §1.2 中完整展示。

---

## 九、附：`market_intel_macro_score_link`（第5条宏观规则）

此规则**不在** `unified_bullish_top20` 的 4 条后置宏观规则中，但存在于市场默认链的标签层。

| 属性 | 值 |
|------|-----|
| 实现类 | `macro_strategies.py:172` → `MarketIntelMacroScoreStrategizer` |
| strategy_category | `macro` |
| 参数 | `threshold=60`, `technical_weight=0.0`, `macro_weight=1.0` |
| display_order | 230 |

**输入**：
- `FilterContext.cache["market_intel_service"]` → 市场情报服务
- `FilterContext.cache["macro_score_scorer"]` → 宏观评分器

**输出**：基于时间感知的宏观证据评分（使用 `EvidencePackBuilder` + `MacroEvidencePreprocessor` + LLM 评分器），包含 `macro_score` 和 `passed` 等字段。

---

## 十、关键文件索引

| 文件 | 内容 |
|------|------|
| `db.py:468-493` | `UNIFIED_BULLISH_TOP20_EXPRESSION` 链定义 |
| `db.py:495-554` | 市场特定链（`zuoyi_with_macro_strict/enhanced`） |
| `db.py:325-382` | `DEFAULT_RULE_METADATA`（规则3/4/5 的元数据） |
| `sql/018_macro_factor_analysis.sql` | 规则1/2 的 DB 部署 |
| `rule_engine.py:728-761` | `evaluate_macro_rules_for_top20()` 执行器 |
| `rule_engine.py:355-367` | 规则1/2 的 `RuleRegistry` 注册 |
| `potential_analysis/strategizer.py:43-106` | `MacroFactorAnalysisStrategizer` |
| `potential_analysis/strategizer.py:113-267` | `EnterprisePotentialAnalysisStrategizer` |
| `potential_analysis/models.py:44-110` | `MacroSnapshot` 数据模型 |
| `potential_analysis/scoring.py` | 五模块评分器 + `EnterprisePotentialResult` |
| `macro_strategies.py:119-169` | `CompanyEventHotSectorStrategizer` |
| `macro_strategies.py:283-342` | `CompanyEventHotNewsStrategizer` |
| `macro_strategies.py:172-280` | `MarketIntelMacroScoreStrategizer` |
| `market_intel/macro_scoring.py:86-140` | `aggregate_rule_scores()` 评分聚合 |
| `api/screen_service.py:432-878` | Top20 后置宏观评估完整流程 |
