# 宏观策略原子规则与规则链编辑设计

## 1. 背景与目标

当前 `MoneyManager/stock_screener` 的原子规则体系只完整覆盖了硬筛选和技术策略，规则链页面也仅支持只读查看，无法在 Web 端直接维护规则链。

本次设计目标：

1. 将以下两类宏观判断正式纳入原子规则体系，作为 `strategy` 的升维扩展，而不是独立的新规则类型：
   - 公司时事与热点板块关联
   - 公司时事与热点新闻关联
2. 保持规则链只有一份 `expression_json`，允许用户创建“只包含宏观策略”的规则链，也允许技术策略和宏观策略混合编排。
3. 避免因为宏观策略引入额外、重复的搜索和 LLM 调用成本。
4. 在规则链页面新增新增、编辑、删除规则链能力，且编排基于现有原子规则，而不是要求用户手写 JSON。

非目标：

1. 不新增第二套独立的 AI 分析引擎。
2. 不把宏观策略改造成期权候选策略。
3. 不改变现有技术策略的命名、基础判定逻辑和默认含义。

## 2. 设计原则

### 2.1 strategy 升维，而不是新增 meta 类型

规则表中仍然只保留两类：

- `filter`
- `strategy`

其中 `strategy` 按执行依赖再分为：

- `technical`：依赖 K 线、成交量、价格等技术数据
- `macro`：依赖 `signal_analysis` 产出的公司时事、热点新闻、热点板块等分析结果

这样做的原因：

1. 产品层面，用户看到的都是“策略”，不会因为内部实现被迫理解新的规则类型。
2. 规则链仍然是一套统一 DSL，不需要人为拆成“初筛链”和“后处理链”。
3. 可以支持只包含宏观策略的规则链。

### 2.2 执行阶段自动分层，但规则链不分裂

规则链仍然只有一个 `expression_json`。执行器根据链中引用的原子规则，自动判断需要准备哪些数据：

1. 仅包含 `filter + technical strategy`
   - 只运行现有筛选流程
2. 包含 `macro strategy`
   - 除现有筛选数据外，再准备 `signal_analysis` 结果
3. 混合链
   - 技术数据和宏观数据都准备完成后，在同一条 `expression_json` 上统一求值

这样，用户维护的是一条链；系统内部做阶段调度，但不把“阶段”暴露为规则链模型的一部分。

### 2.3 优先复用 analysis 结果，避免重复 AI 调用

宏观策略本质上依赖 `signal_analysis`，因此不能为每条宏观策略单独走一次搜索+LLM。

方案：

1. 建立股票维度的共享分析缓存。
2. 股票筛选和期权实验室统一复用这份缓存。
3. 只有缓存未命中时才触发一次 `signal_analysis`。

## 3. 数据模型调整

### 3.1 screening_rule_metadata

保留原有字段，并新增：

- `strategy_category VARCHAR(16) NULL`
  - `filter` 类型可为空
  - `strategy` 类型可取：
    - `technical`
    - `macro`

默认规则中：

- 现有技术策略统一标为 `technical`
- 新增两条宏观策略：
  - `company_event_hot_sector_link`
    - `rule_name`: 公司时事与热点板块关联
    - `rule_type`: strategy
    - `strategy_category`: macro
    - `implementation`: CompanyEventHotSectorStrategizer
  - `company_event_hot_news_link`
    - `rule_name`: 公司时事与热点新闻关联
    - `rule_type`: strategy
    - `strategy_category`: macro
    - `implementation`: CompanyEventHotNewsStrategizer

### 3.2 screening_rule_chains

不新增第二套表达式字段，继续使用单一：

- `expression_json`

保留现有 `timeframe`、`enabled`、`priority` 语义不变。

### 3.3 共享 signal_analysis 缓存

新增或改造共享缓存表，缓存键按用户确认的口径设计：

- `market + code + timeframe + analysis_profile + trade_date`

建议表名：

- `signal_analysis_cache`

核心字段：

- `cache_key`
- `market`
- `code`
- `timeframe`
- `analysis_profile`
- `trade_date`
- `analysis_status`
- `reliability_score`
- `confidence_score`
- `signal_bias`
- `summary`
- `company_events`
- `company_hot_news`
- `market_hot_news`
- `news_impact`
- `hot_sectors`
- `matched_hot_sectors`
- `hot_sector_mark`
- `hot_sector_reason`
- `source_urls`
- `evidence_links`
- `factor_citations`
- `data_gaps`
- `model`
- `raw_response`
- `created_at`
- `updated_at`

说明：

1. 该缓存是“股票级别共享缓存”，不是“规则链级缓存”，也不是“候选策略级缓存”。
2. `option_macro_analysis_cache` 继续存在以兼容现有期权链路，但其底层应改为优先复用共享 `signal_analysis_cache`。

## 4. 宏观策略判定逻辑

### 4.1 公司时事与热点板块关联

实现类：

- `CompanyEventHotSectorStrategizer`

依赖字段：

- `company_events`
- `hot_sectors`
- `matched_hot_sectors`
- `hot_sector_mark`
- `hot_sector_reason`

判定建议：

1. 若 `company_events` 为空：
   - 返回 `skip`
   - reason：缺少公司时事，无法判断与热点板块的关联
2. 若热点板块信息为空：
   - 返回 `skip`
   - reason：缺少热点板块数据，无法判断是否形成板块共振
3. 若 `hot_sector_mark` 为 `重点` 或 `相关`：
   - 返回 `pass`
4. 其他情况：
   - 返回 `fail`

输出详情建议包含：

- `company_events`
- `matched_hot_sectors`
- `hot_sector_mark`
- `hot_sector_reason`

### 4.2 公司时事与热点新闻关联

实现类：

- `CompanyEventHotNewsStrategizer`

依赖字段：

- `company_events`
- `company_hot_news`
- `market_hot_news`
- `news_impact`
- `news_sources`

判定建议：

1. 若 `company_events` 为空：
   - 返回 `skip`
   - reason：缺少公司时事，无法判断与热点新闻的关联
2. 若公司新闻和市场热点新闻都为空：
   - 返回 `skip`
   - reason：缺少热点新闻数据，无法判断新闻是否验证公司时事
3. 若 `news_impact` 明确为 `利好`、`利空`、`偏利好`、`偏利空`：
   - 返回 `pass`
4. 其他情况：
   - 返回 `fail`

输出详情建议包含：

- `company_events`
- `company_hot_news`
- `market_hot_news`
- `news_impact`
- `news_sources`

### 4.3 skip 语义

宏观策略的数据缺失不能简单等同为失败。沿用现有规则引擎语义：

- `filter`: `PASS` 与 `SKIP` 为真
- `strategy`: 目前只有 `satisfied=True/False`

为兼容宏观策略，建议将 `strategy` 输出结构扩展出三态结果：

- `pass`
- `fail`
- `skip`

或者保持 `strategy` 仍输出 `satisfied`，但在 `details` 中标识 `skipped=true`，并让规则引擎把宏观策略的 skip 视为真值。

推荐前者，语义更一致，也便于前端展示。

## 5. 统一执行路径

### 5.1 规则链依赖分析

规则链执行前，先收集链中被引用的原子规则，判断：

1. 是否需要 K 线/技术数据
2. 是否需要宏观分析缓存或实时 `signal_analysis`

### 5.2 单股与批量筛选流程

统一执行步骤：

1. 读取规则链与原子规则
2. 按规则依赖准备基础数据
3. 若链中存在 `macro strategy`
   - 先查 `signal_analysis_cache`
   - 未命中则调用 `signal_analysis`
   - 将结果写回缓存
4. 执行所有被引用的 `filter/strategy`
5. 在同一条 `expression_json` 上求值
6. 输出筛选明细、AI 分析结果、宏观策略命中结果

### 5.3 期权实验室复用

期权实验室的宏观分析改为：

1. 优先查共享 `signal_analysis_cache`
2. 命中则直接构造期权侧宏观摘要与评分解释
3. 未命中才触发 `signal_analysis`
4. 期权自身的评分公式不变，但数据源改为共享缓存优先

## 6. 规则链页面改造

### 6.1 页面目标

规则链页面从“只读配置页”改造成“可维护规则链页面”。

支持：

1. 新增规则链
2. 编辑规则链
3. 删除规则链

### 6.2 原子规则展示

原子规则表新增展示列：

- `rule_key`
- `rule_name`
- `rule_type`
- `strategy_category`
- `implementation`
- `enabled`
- `display_order`

其中：

- 技术策略显示为 `技术策略`
- 宏观策略显示为 `宏观策略`

### 6.3 规则链编辑交互

不要求用户手写整段 JSON。页面提供基于现有原子规则的编排：

1. 选择市场
2. 选择周期
3. 填写：
   - 规则链 Key
   - 规则链名称
   - 优先级
   - 是否启用
   - 描述
4. 从现有原子规则中构造表达式：
   - `ref`
   - `and`
   - `any`
   - `all_enabled`
   - `any_enabled`

页面右侧或下方实时展示生成后的 `expression_json` 预览，便于调试。

### 6.4 删除保护

删除规则链时至少加两层保护：

1. 当前默认生效链不允许直接删除
2. 若未来有任务记录显式引用该链，可进一步增加引用保护或确认提示

## 7. API 调整

在现有 `/api/rules` 基础上，新增：

- `POST /api/rules/chains`
  - 新增规则链
- `PUT /api/rules/chains/{market}/{timeframe}/{chain_key}`
  - 编辑规则链
- `DELETE /api/rules/chains/{market}/{timeframe}/{chain_key}`
  - 删除规则链

返回结构继续保持中文友好错误信息。

`GET /api/rules` 返回内容扩展：

1. 原子规则中包含 `strategy_category`
2. 规则链中返回完整 `expression_json`

## 8. 测试要求

### 8.1 规则引擎

1. 纯技术链保持现有行为不变
2. 纯宏观链可以独立执行
3. 技术+宏观混合链可以统一求值
4. 宏观策略数据缺失时按 `skip` 处理，不误伤整体链路

### 8.2 缓存

1. 同 `market + code + timeframe + analysis_profile + trade_date` 命中缓存时不触发 AI
2. 股票筛选和期权实验室对同一只股票复用同一缓存
3. `force_refresh` 可跳过缓存并刷新结果

### 8.3 Web API

1. 规则链新增成功
2. 规则链编辑成功
3. 规则链删除成功
4. 删除默认生效链时返回业务错误
5. 非法表达式、非法规则引用、跨市场不兼容链正确报错

### 8.4 前端

1. 规则链列表可刷新并展示新增字段
2. 可从现有原子规则新增规则链
3. 可编辑已有规则链
4. 可删除试跑链
5. 表单错误和后端业务错误能正确显示中文提示

## 9. 风险与兼容性

1. `strategy` 从“纯技术策略”升维为“技术策略 + 宏观策略”后，执行器必须避免假设所有策略都依赖 K 线。
2. `signal_analysis` 结果复用到股票与期权两条链路时，要统一字段含义，避免同字段在两处解释不一致。
3. 规则链页面改成可写后，必须保留默认链保护，避免页面误操作导致整个市场无可用规则链。
4. 共享缓存一旦引入，应优先保证读路径稳定，再逐步减少 `option_macro_analysis_cache` 的重复逻辑。
