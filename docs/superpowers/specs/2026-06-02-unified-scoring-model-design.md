# 统一评分模型设计（方案 A：保守合并）

**日期：** 2026-06-02
**状态：** 设计完成，待评审
**目标：** 将两套独立评分体系合并为以五因素模型为唯一出口的统一评分引擎

---

## 1. 问题诊断

当前系统中存在**两套独立运行的评分体系**，互不协同：

| 体系 | 类名 | 权重 | 评分方式 | 输出 |
|------|------|------|----------|------|
| A | `MarketIntelMacroScoreStrategizer` | 0.6技术 + 0.4宏观 | LLM 评分器 | `macro_score` + pass/fail |
| B | `EnterprisePotentialAnalysisStrategizer` | M30+I25+C25+V10+T10 | 规则型(默认)/LLM型(可选) | `total_score` + BUY/WATCH/SKIP |

**核心问题：**

1. **双重评分无协同** — 同一只股票两套评分可能矛盾（A 通过 B 不通过），最终通过逻辑不透明
2. **三维度重复评估** — 宏观、行业、公司事件分别被 A 的 6 维子评分和 B 的 M/I/C 模块独立评估，浪费 LLM 调用
3. **技术面定义分裂** — A 的"技术面"是 LLM 综合判断，B 的"Trading"是结构化指标，完全不互通
4. **缺失模块 50 分中性补齐** — 数据不全的股票可获得中等分数通过，掩盖信息缺失风险
5. **阈值无市场差异化** — HK/US/A 三市场使用相同阈值

---

## 2. 目标架构

### 2.1 数据流

```
数据采集层（不变）
├─ SignalAnalysis LLM ──→ 时事/热点/新闻
├─ MarketIntel Service ──→ 市场情报证据
├─ MacroFactor Provider ──→ 宏观因子快照
├─ Financial Data ──→ 企业财务+估值
└─ K-line Data ──→ 交易行为指标

统一评分引擎（EnterprisePotentialAnalysisStrategizer 增强版）
├─ MacroScorer（规则型）+ MarketIntel LLM 增强（±10）
├─ IndustryScorer + SignalAnalysis 热点标签注入
├─ CompanyScorer + LLM 事件/新闻增强 + MarketIntel 公司事件
├─ ValuationScorer（不变）
└─ TradingScorer + 规则链策略器信号增强

唯一输出
→ total_score + module_scores + decision + holding_period + confidence
```

### 2.2 核心原则

- **MarketIntelMacroScoreStrategizer 降级** — 不再独立输出 pass/fail，改为将 LLM 证据包写入 `FilterContext` 缓存供 EnterprisePotential 读取
- **单一评分出口** — `EnterprisePotentialAnalysisStrategizer` 为唯一评分器
- **LLM 增强为可选** — MarketIntel Service 不可用时规则型评分正常工作（fail-open）
- **aggregate_rule_scores 读取五因素** — 废弃 0.6/0.4 二因素加权逻辑

---

## 3. 评分模型细节

### 3.1 权重体系

| 模块 | 权重 | 评分方式 | LLM 增强源 |
|------|------|----------|------------|
| M — 宏观 | 30% | 规则型阈值（LPR/CPI/PMI/M2/VIX/DXY） | MarketIntel LLM 宏观评分 ±10 |
| I — 行业 | 25% | 标签型（热点标签+周期阶段+政策） | SignalAnalysis hot_sectors |
| C — 企业 | 25% | 阈值型（ROIC/毛利/增速/负债） | SignalAnalysis event_impact±10, news_validation+5; MarketIntel company_event_strength±5 |
| V — 估值 | 10% | 阈值型（PE/PB/PEG/EV_EBITDA） | 无 |
| T — 交易 | 10% | 阈值型（涨跌/波动/趋势/均线） | 规则链策略器 pass 信号加分 |

### 3.2 缺失模块处理

**从"50分中性补齐"改为"置信度惩罚"：**

- 缺失模块不参与加权求和（不贡献分数也不贡献权重）
- 仅用可用模块计算加权总分
- `confidence_score` = 可用模块数 / 5 × 100
- 当 `confidence_score < 40`（< 2 模块可用）时 → `result="skip"`，不输出总分
- 总分旁标注 "基于 N/5 模块"

示例：Macro=70, Company=80, Trading=60, Valuation和Industry缺失
→ 总分 = (70×0.30 + 80×0.25 + 60×0.10) / (0.30+0.25+0.10) = 72.3
→ confidence = 3/5 = 60%

### 3.3 市场差异化阈值

| 市场 | BUY 阈值 | WATCH 阈值 | 理由 |
|------|----------|------------|------|
| HK | ≥ 75 | ≥ 65 | 港股波动大，宏观敏感，降低 WATCH 门槛 |
| US | ≥ 75 | ≥ 70 | 估值中枢高，企业质量分化大 |
| A | ≥ 75 | ≥ 70 | 政策驱动强，维持标准 |

阈值可通过策略器 `params` 中的 `threshold` 参数覆盖。

### 3.4 决策 & 持有周期

保持现有 `holding_period.py` 逻辑：
- `total_score ≥ 75` → BUY
- `total_score ≥ threshold` → WATCH
- 其余 → SKIP
- 持有周期由主导模块映射：trading=1-3月, industry=3-9月, macro=6-12月, company=12-24月, valuation=12-36月

---

## 4. aggregate_rule_scores 重构

**旧逻辑：** 扫描 `filter_details`，strategy 类规则 pass→100/fail→0 平均得 `technical_score`；strategy_category="macro" 中提取 `macro_score`；0.6×技术+0.4×宏观 → `final_score`。

**新逻辑：** 直接读取 EnterprisePotential 输出的 `total_score` + `module_scores`：

```python
def aggregate_rule_scores(rows, ...):
    # 优先查找五因素分数
    for row in rows:
        details = row.get("details", {})
        if "total_score" in details:
            return {
                "technical_score": details.get("trading_score"),
                "macro_score": details.get("macro_score"),
                "final_score": details["total_score"],
                "module_scores": {
                    k: details.get(f"{k}_score")
                    for k in ["macro", "industry", "company", "valuation", "trading"]
                },
                "decision": details.get("decision"),
                "holding_period": details.get("holding_period"),
                "confidence": details.get("confidence_score"),
            }
    # fallback: 旧逻辑（不启用 B 体系的规则链）
    return _legacy_aggregate(rows, technical_weight, macro_weight)
```

**写入 DB 的字段映射：**
- `technical_score` → Trading 模块分
- `macro_score` → Macro 模块分
- `final_score` → total_score
- `score_details` JSON 新增 `module_scores` / `decision` / `holding_period` / `confidence`

---

## 5. 报告结构优化

### 5.1 报告节变更

| 节 | 当前 | 目标 |
|----|------|------|
| 一 | 核心结论 | 核心结论 + **五因素概览**（平均分、最弱维度、数据覆盖率） |
| 二 | 复核结果总览表 | 总览表（列从 5→8，新增五因素/决策/持有建议） |
| 三 | 主力流出风险观察 | **🆕 五因素评分明细**（仅 BUY/WATCH 股票展开） |
| 四 | 热点板块分析 | 主力流出风险观察（原第三节后移） |
| 五 | 个股详细分析 | 热点板块分析 |
| 六 | — | 个股详细分析 |

### 5.2 总览表列结构（5→8 列）

旧：股票代码 | 名称 | 综合评分 | 方向判断 | 热点匹配 | 简明结论

新：**股票代码** | **名称** | **综合评分** | **五因素**(M·I·C·V·T) | **决策** | **持有建议** | **热点匹配** | **简明结论**

### 5.3 五因素评分明细节（仅 BUY/WATCH）

对每个 BUY/WATCH 股票输出模块表格：

```
| 模块 | 得分 | 权重 | 贡献 | 状态 |
|------|------|------|------|------|
| 宏观 | 68 | 30% | 20.4 | ✅ 基于 7 因子 |
| 行业 | 85 | 25% | 21.3 | ✅ 创新药热点 + 强政策 |
| ...  | ... | ...  | ...  | ...  |

总分: 72.5 (阈值 65) → WATCH | 持有建议: 3-9个月 | 置信度: 100% (5/5)
```

---

## 6. CSV 列精简（51→38 列）

### 6.1 删除（16 列）

| 列 | 原因 |
|----|------|
| 信号可靠性评分口径 | 每行重复常数 → 放文档脚注 |
| 模型置信度口径 | 同上 |
| 辅助方向判断口径 | 同上 |
| 热点板块标记口径 | 同上 |
| 资金与盘面观察 | 长文本 → 报告中展示 |
| 资金流向数据 | 原始明细 → DB 可查 |
| 盘口数据 | 原始明细 → DB 可查 |
| 龙虎榜数据 | 原始明细 → DB 可查 |
| 成交量分布数据 | 原始明细 → DB 可查 |
| 左一中位线日期 | 可从支撑区间推算 |
| 热点板块匹配理由 | 长文本 → 报告中展示 |
| 热点板块来源 | 已合并到「信息来源」 |
| 热点板块关联度 | 语义包含在「热点板块标记」中 |
| 引用来源 | JSON blob → CSV 不可读 |
| 因素引用 | JSON blob → CSV 不可读 |
| 新闻来源 | 已合并到「信息来源」 |

### 6.2 合并（6→2 列）

| 旧列 | 合并为 |
|------|--------|
| 市场热点新闻 + 公司热点新闻 | **相关新闻** |
| AI识别热点板块 + 匹配热点板块 | **热点板块**（匹配成功的打★前缀） |

### 6.3 新增（5 列）

**五因素总分** | **五因素明细**(M·I·C·V·T) | **决策** | **建议持有周期** | **评分置信度**

---

## 7. 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `potential_analysis/scoring.py` | 增强 | 缺失模块置信度惩罚；MacroScorer LLM 增强参数；市场差异化阈值 |
| `potential_analysis/strategizer.py` | 增强 | `_inject_signal_analysis()` → `_inject_llm_enhancements()`，同时读取 signal_analysis 和 market_intel 缓存 |
| `macro_strategies.py` | 重构 | `MarketIntelMacroScoreStrategizer` 不再独立评分，改为向 FilterContext 写入 LLM 证据包 |
| `rule_engine.py` | 简化 | 移除独立的 `requires_market_intel_macro_score()`，统一到 `requires_enterprise_potential()` |
| `api/screen_service.py` | 简化 | 移除 MarketIntel Service 独立注入；aggregate_rule_scores 改用五因素分数 |
| `market_intel/macro_scoring.py` | 保留 | 不作为 strategizer 暴露，保留 LLM 评分能力供增强调用 |
| `signal_analysis/models.py` | 精简 | `to_csv_columns()` 删除口径列、合并新闻/热点列 |
| `scheduled_daily_job.py` | 精简 | CSV 列定义删减+合并+新增五因素列 |
| `signal_analysis/chain.py` | 精简 | `AI_CSV_COLUMNS` 适配新列结构 |
| 报告模板 | 增强 | 总览表 5→8 列；新增五因素明细节；核心结论增加五因素概览 |

---

## 8. 迁移计划（两步）

### Step 1：评分模型增强 + 合并 MarketIntel

- 实现缺失模块置信度惩罚、市场差异化阈值、LLM 增强参数
- `MarketIntelMacroScoreStrategizer` 直接改为向 `FilterContext` 写入 `market_intel:{code}` 缓存
- 移除其独立 pass/fail 输出，`apply()` 始终返回 `satisfied=True`（不阻断链）
- `aggregate_rule_scores` 改为读取五因素分数，检测不到时 fallback 旧逻辑
- **一次性上线验证**

### Step 2：报告升级 + CSV 精简 + 废弃代码清理

- 报告模板增加五因素明细节
- CSV 列删减/合并/新增（51→38 列）
- 移除 `aggregate_rule_scores` 旧 `0.6/0.4` 加权逻辑
- 移除 `MarketIntelMacroScoreStrategizer` 中不再使用的独立评分分支

---

## 9. 兼容性保证

| 维度 | 策略 |
|------|------|
| **DB 字段** | `technical_score`/`macro_score`/`final_score` 列不变；`score_details` JSON 新增键，旧消费者忽略未知键 |
| **不启用 B 体系的规则链** | `aggregate_rule_scores` 检测不到五因素分数时 fallback 旧逻辑；`RuleEngine.requires_enterprise_potential()` 返回 False 时 B 体系不激活 |
| **CSV 导出** | 现有列不变（除被删的 16 列），五因素列追加末尾；旧脚本按列名读取不受影响 |
| **MarketIntel 规则引用** | `rule_key` 保留在 `screening_rule_metadata` 表中，DSL 引用不变，仅 `apply()` 行为变更 |
| **飞书通知** | 摘要文本和文件发送逻辑不变 |

---

## 10. 风险 & 缓解

| 风险 | 概率 | 缓解 |
|------|------|------|
| MarketIntel LLM 证据质量下降导致 Macro/Company 模块分波动 | 低 | LLM 增强仅 ±5~10 修正，不颠覆规则评分；不可用时 fail-open |
| 缺失模块惩罚导致通过率骤降 | 中 | Step 1 先上线观察通过率，可通过 `confidence_score` 阈值调节 |
| 市场差异化阈值导致结果不可比 | 低 | 差异仅 5 分（65 vs 70），仅影响 WATCH 决策；跨市比较用原始 total_score |
