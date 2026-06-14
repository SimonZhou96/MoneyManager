# 个股筛选器 UI 优化方案

> 基于现有暗色金融终端风格的完整交互重设计  
> 分析基准：MoneyManager v0.1.0 代码库（2026-06-13）  
> 外部研究：Deep-research 工作流交叉验证了 9 项设计主张（105 agents, 851 tool calls）

### 研究验证摘要

| 验证点 | 来源 | 置信度 | 对本文档的影响 |
|--------|------|--------|---------------|
| 用零替代缺失数据 → 0% 用户正确识别 | UMD/MIT 实证研究 (2005) | High | **验证 P0-1 必要性** — 当前"0分/未补齐"混用会误导 100% 用户 |
| 三态 metric validity (ok/unknown/error) | GitHub, GitLab, UX StackExchange | High | **采纳为本文档的数据状态模型** |
| 异步状态机 none→loading→success/error→none | react-progress-state (npm) | High | **采纳为筛选进度状态机设计** |
| 金融 UI 四原则：专业可信/数据导向/现代简约/高效易用 | Stock_analyzer design.md | High | **与本文档设计方向一致** |
| SHAP per-prediction 贡献分解 | explainerdashboard (GitHub) | High | **验证 P1-5 评分公式拆解的交互模式** |

---

## 一、信息架构优化

### 1.1 新页面区块规划

按用户操作流程，将原有单一面板拆分为 **6 个逻辑区块**，自上而下展开：

```
┌──────────────────────────────────────────────────────────────┐
│ ① 筛选输入区                                                   │
│ [市场] [周期] [规则链] [股票搜索...................] [开始筛选]   │
├──────────────────────────────────────────────────────────────┤
│ ② 股票识别确认卡 (选中后展示)                                    │
│ ┌──────────┐ ┌──────────┐ ┌────────────┐ ┌────────────────┐  │
│ │ 腾讯控股  │ │ HK.00700 │ │ 互联网服务  │ │ 市值4.2万亿 PE24│  │
│ └──────────┘ └──────────┘ └────────────┘ └────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│ ③ 数据获取状态 / 数据诊断 (筛选进行中或完成后展示)                │
│ ═══════════════════════░░░░░ 60% — 正在执行规则评估...          │
│ ● 股票识别 ✓  ● K线获取 ✓  ● 技术指标 ⏳  ○ 宏观数据 ○ AI分析    │
├──────────────────────────────────────────────────────────────┤
│ ④ K线图表分析区                                                │
│ ┌──────────────────────────────────────────────────────────┐  │
│ │  腾讯控股 (00700)                          1d             │  │
│ │  ▓▓▓▓▓▓▓▓  K 线图表 (lightweight-charts)                 │  │
│ │  ▓▓▓▓▓▓▓▓  (含买卖点标注、EMA 叠加)                        │  │
│ └──────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│ ⑤ 规则评分报告 (核心结果区)                                      │
│ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐          │
│ │ 综合评分  │ │ 技术维度  │ │ 宏观维度  │ │ 事件热度  │          │
│ │  73.0    │ │  50.0    │ │  18.0    │ │  5.0     │          │
│ │ 🟢 看涨   │ │ 12通过   │ │ 2通过    │ │ ⚠️ 部分   │          │
│ └──────────┘ └──────────┘ └──────────┘ └──────────┘          │
│                                                                │
│ ▸ 规则链明细 — 21条技术规则 + 4条宏观规则                        │
│   ┌─ zuoyi_signal (左一战法) ──────────────────────────┐       │
│   │ 🟢 通过 │ 方向:看涨 │ 所需:K线日线×15              │       │
│   │ 数据: 左一价=85.2 > 前3日均价=82.1 (+3.8%)        │       │
│   │ 权重:8 │ 对评分贡献:+8.0                           │       │
│   └──────────────────────────────────────────────────┘       │
│                                                                │
│ ▸ 数据诊断摘要 (默认折叠)                                        │
├──────────────────────────────────────────────────────────────┤
│ ⑥ 历史记录 (Tab 切换)                                          │
│ ┌──────────────────────────────────────────────────────────┐  │
│ │ 时间 │ 规则链 │ 结果 │ 综合分 │ 失败原因 │ 操作             │  │
│ │ 06-12│ 统一看涨│ 🟢  │ 73.0  │    —    │ 🔄重筛 📋复制    │  │
│ │ 06-10│ 统一看涨│ 🔴  │  —    │ K线不足  │ 🔄重筛 📋复制    │  │
│ └──────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### 1.2 信息优先级分层

| 层级 | 展示策略 | 包含内容 |
|------|---------|---------|
| **L1 — 即时可见** | 筛选完成后立即展示，不可折叠 | 综合评分、通过/未通过状态、K线图表、评分公式 |
| **L2 — 默认展开** | 筛选完成后展示，可折叠 | 规则链明细、评分卡片组、数据诊断摘要 |
| **L3 — 手动展开** | 默认折叠，点击展开 | 单条规则的原始 details、完整错误堆栈、API 耗时 |

---

## 二、视觉样式优化

### 2.1 暗色金融终端设计语言（保持现有，局部增强）

**色彩体系**（沿用现有 CSS 变量并扩展）：

```css
:root {
  --bg-root: #0a0e14;
  --bg-card: #141920;
  --bg-input: #1a1f2a;
  --border: #2a3040;
  --border-focus: #c9a84c;       /* 金色 — 仅用于聚焦态 */
  --text-primary: #e8e0d0;
  --text-secondary: #c0b8a0;
  --text-muted: #8a8070;
  --gold: #c9a84c;
  --gold-light: #e8c560;
  --success: #22c55e;
  --danger: #ef4444;
  --warning: #f59e0b;            /* 新增 — 黄色用于数据缺失 */
  --info: #3b82f6;               /* 新增 — 蓝色用于已计算 */
  --neutral: #6b7280;            /* 新增 — 灰色用于未计算 */
}
```

### 2.2 金色强调色使用纪律

**原则：金色不是装饰色，是"信号色"——只在传达核心结论时使用。**

| 使用场景 | 是否用金色 | 理由 |
|---------|-----------|------|
| 综合评分数字 | ✅ 是 | 核心结论，唯一需要突出的值 |
| 主操作按钮 | ✅ 是 | 引导用户行动 |
| 聚焦态边框 | ✅ 是 | 当前选中/活跃态 |
| 标题分隔装饰线 | ✅ 限量使用 | 每区块最多 1 处 |
| 筛选状态"通过" | ❌ 否，用绿色 | 状态语义应绿/红通道 |
| 规则通过标记 | ❌ 否，用绿色 | 保持绿/红通道一致性 |
| 普通指标标签 | ❌ 否，用 muted 灰 | 避免金色疲劳 |
| K 线涨跌 | ❌ 否，用绿/红 | 行业惯例 |
| 数据状态标签 | ❌ 否，用独立色彩 | 蓝/黄/灰各有语义 |

### 2.3 按钮层级设计

```
[⚡ 开始筛选]   — 金色渐变 primary 按钮（唯一主操作）
[🔄 重试]       — 金色描边 secondary 按钮
[📊 查看诊断]   — 灰色描边 tertiary 按钮
[📋 复制参数]   — 小号 link 按钮，无边框
[▸ 展开详情]    — 纯文字 + 箭头，无边框
```

### 2.4 状态标签系统

新增 **4 种数据完整性状态标签**，与现有的通过/未通过标签形成完整体系：

```tsx
// 现有：结果状态
<StatusBadge value="passed" />   // 🟢 通过
<StatusBadge value="failed" />   // 🔴 未通过

// 新增：数据完整性状态
<DataStatusBadge status="computed" />      // 🔵 已计算
<DataStatusBadge status="not_computed" />  // ⚪ 未计算
<DataStatusBadge status="missing" />       // 🟡 数据缺失
<DataStatusBadge status="error" />         // 🔴 接口失败
```

CSS：

```css
.data-status {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  line-height: 1.4;
}
.data-status.computed     { color: #3b82f6; background: rgba(59,130,246,0.1); }
.data-status.not_computed { color: #6b7280; background: rgba(107,114,128,0.1); }
.data-status.missing      { color: #f59e0b; background: rgba(245,158,11,0.1); }
.data-status.error        { color: #ef4444; background: rgba(239,68,68,0.1); }
```

### 2.5 空状态 / 错误状态设计

| 场景 | 现有问题 | 改进方案 |
|------|---------|---------|
| K线无数据 | 空白覆盖层仅显示文字 | 带原因说明 + 操作建议的空状态卡片 |
| K线加载中 | 仅显示文字 loading | shimmer 骨架屏（420px 高度，40 条模拟 K 线柱） |
| K线接口失败 | 红色文字覆盖 | 错误卡片：错误类型 + 原因 + [🔄 重试] 按钮 |
| 规则全部无法计算 | 显示"失败" | 量化说明："21 条规则中 0 条通过，18 条无法计算（缺少K线数据），3 条未通过" |
| 股票未找到 | 搜索框空结果 | 明确文案："未找到匹配「XXX」的股票，请尝试切换市场或使用代码搜索" |
| 多条候选匹配 | 静默选第一条 | 下拉展示全部候选，支持键盘导航确认 |

### 2.6 长文本溢出解决方案

| 内容类型 | 解决方案 | 实现方式 |
|---------|---------|---------|
| 板块名称（>15 字） | Tooltip + 省略号 | CSS `text-overflow: ellipsis` + HTML `title` |
| 规则链名称（长文本） | 多行展示 | `white-space: normal; word-break: break-word` |
| 规则原因（>100 字） | 展开/收起 | 默认显示前 1 行 + "显示全部..." 按钮 |
| 数据来源 | 友好名称映射 | `web_backend` → `Web 后端直连`；`yfinance` → `Yahoo Finance` |
| 股票代码 | 一键复制 | 旁置 📋 图标，`navigator.clipboard.writeText()` |
| 评分明细 JSON | 折叠面板 | `<details>` + `<summary>` 或自定义折叠 |

---

## 三、数据完整性展示

### 3.1 数据诊断模块

在进度条下方嵌入 **6 项诊断检查点**，筛选完成后展示：

```
┌─ 数据诊断 ────────────────────────────────────────────────┐
│ ● 股票识别    🔵 已识别 — 腾讯控股 (HK.00700)               │
│ ● K线数据     🔵 已获取 — 200 条日线 (2026-01-02 ~ 06-12)  │
│ ● 技术指标    🔵 已计算 — 21 条规则完成评估                  │
│ ● 宏观数据    🟡 部分补齐 — 企业潜力分析已执行               │
│              🔴 热点新闻匹配失败 (Tavily API 30s 超时)       │
│ ● 行业数据    🔵 已获取 — 互联网内容与服务                    │
│ ● 后端状态    🔵 正常 — 总耗时 2.3s, 无异常                  │
└──────────────────────────────────────────────────────────┘
```

每项诊断项的数据结构：

```typescript
interface DiagnosticItem {
  key: string
  label: string                      // 中文名称
  status: 'computed' | 'not_computed' | 'missing' | 'error'
  detail: string                     // 展示详情
  suggestion?: string                // 失败时的建议操作
}
```

### 3.2 四种数据状态判定逻辑

| 状态 | 含义 | 前端展示 | 判据 |
|------|------|---------|------|
| **已计算为 0 分** | 规则执行完成，得分确实为 0 | `0.0`（白色） | `result !== undefined && score === 0` |
| **未计算** | 规则未执行（不在规则链中或条件不满足） | `—`（灰色） | `result === undefined \|\| result === null \|\| status === 'not_executed'` |
| **数据缺失** | 规则需要的数据不存在 | `数据缺失`（黄色标签） | `status === 'missing_data' \|\| required_field_unavailable` |
| **接口失败** | 外部 API 调用异常 | `接口失败`（红色标签） | `status === 'api_error' \|\| error_message 非空` |

### 3.3 失败原因展示文案

```yaml
# 场景 1: K线数据获取失败
title: "K线数据获取失败"
detail: "Yahoo Finance API 返回 404 — 该股票代码格式与 Yahoo Finance 不兼容"
suggestion: "请尝试切换周期为 1wk（周线），或检查股票代码是否正确。部分港股需 4 位数字代码。"

# 场景 2: 宏观数据未补齐
title: "宏观数据部分缺失"
detail: "EnterprisePotentialAnalysis 需要至少 5 只同行业对标股票，当前仅找到 2 只"
suggestion: "可先执行「仅技术面分析」，切换至更多成分股的市场后重试。"

# 场景 3: 外部 API 超时
title: "热点新闻匹配超时"
detail: "Tavily Search API 在 30s 后未响应，已自动回退"
suggestion: "不影响核心筛选结果。新闻热度相关规则已标记为「无法计算」"

# 场景 4: 规则链不支持
title: "规则链与市场不匹配"
detail: "规则链「unified_bullish_top20」未在当前市场(HK)注册"
suggestion: "请切换到 A股 或 美股 市场，或选择「默认链」使用该市场通用规则"

# 场景 5: 股票代码无效
title: "股票代码无法识别"
detail: "输入的代码「ABCDEF」在所有市场中均未匹配到有效股票"
suggestion: "请确认股票代码格式：港股 5 位数字、美股字母代码、A股 6 位数字"
```

---

## 四、交互流程优化

### 4.1 筛选按钮状态机

```
[开始筛选] ──(click)──▶ [筛选中...] ──(SSE step update)──▶ [完成 ✅]
                │                      │
                │                      ├─ init (5%)
                │                      ├─ stock_lookup (10%)
                │                      ├─ kline_fetch (25%)
                │                      ├─ rule_eval (40-80%)
                │                      ├─ read_result (85%)
                │                      ├─ save_result (95%)
                │                      └─ done (100%)
                │
                └──(API error)──▶ [失败 ❌] ──▶ ErrorRecoveryCard
```

实现要点：
- 按钮 `disabled` + 文字变为"筛选中..." + 左侧 spinner
- 禁止重复提交（`screening === true` 时忽略点击）
- K 线区域显示骨架屏（替代空白）
- 进度条实时更新百分比 + 步骤描述
- 失败时进度条变红

### 4.2 图表骨架屏

```tsx
function KlineSkeleton() {
  return (
    <div className="kline-chart-panel">
      <div className="kline-chart-heading">
        <div className="skeleton-cell w-32" style={{height:16}} />
        <div className="skeleton-cell w-16" style={{height:16}} />
      </div>
      <div className="kline-chart-wrap" style={{height:420}}>
        <div style={{display:'flex', alignItems:'flex-end', height:'100%', gap:3, padding:'20px 10px'}}>
          {Array.from({length:40}).map((_, i) => (
            <div key={i} className="skeleton-cell"
              style={{
                flex:1, minWidth:3,
                height: `${25 + Math.sin(i*0.25)*15 + (i%5)*6}%`,
                borderRadius: '2px 2px 0 0'
              }}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
```

### 4.3 失败恢复操作

筛选失败后，用 **可操作的错误卡片** 替代空白：

```
┌─ ⚠️ 筛选未完成 ──────────────────────────────────────────┐
│                                                           │
│ 错误: K线数据不足 — 仅获取到 3 条日线记录,                   │
│ 技术指标计算至少需要 20 条数据。                              │
│                                                           │
│ 操作:                                                     │
│ [🔄 重新筛选]  [📊 查看数据诊断]  [📅 切换至周线(1wk)]       │
│ [⚙️ 仅执行技术面分析]  [📋 复制错误详情]                     │
└──────────────────────────────────────────────────────────┘
```

### 4.4 股票名称多候选确认交互

当前仅提供下拉点击选择。增强为：

```
搜索框: [小米                ] 🔍
┌────────────────────────────────────────────┐
│ 🏢 小米集团-W       01810.HK   消费电子     │ ← 键盘↑↓导航
│ 🏢 小米机器人         — .HK    机器人       │   Enter 确认
│ 🏢 小米科技           — .A     科技         │
├────────────────────────────────────────────┤
│ 找到 3 个匹配项，使用 ↑↓ 键导航，Enter 确认   │
└────────────────────────────────────────────┘
```

交互细节：
- 每条结果：名称(加粗) + 代码(等宽字体) + 板块/市场(金色标签)
- `onKeyDown` 支持 ↑↓ 移动高亮 + Enter 选择 + Esc 关闭
- 候选 > 10 条时显示"显示前 10 条，请缩小搜索范围"
- 鼠标 hover 高亮，点击选中

---

## 五、筛选报告设计

### 5.1 评分概览卡片组

```
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│   综合评分    │ │   技术维度    │ │   宏观维度    │ │   事件热度    │
│              │ │              │ │              │ │              │
│    73.0     │ │    50.0      │ │    18.0      │ │     5.0      │
│   🟢 看涨    │ │  权重 40%    │ │  权重 30%    │ │  权重 10%    │
│              │ │              │ │              │ │              │
│ 评分状态 🔵  │ │ 12 🟢 6 🔴  │ │  2 🟢 1 🔴  │ │  1 🟢 0 🔴  │
│              │ │  3 ⚪ 无法   │ │  1 🟡 缺失  │ │  1 🟡 缺失  │
└──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘
```

设计规则：
- 综合评分卡片左边框用金色 3px（`border-left: 3px solid #c9a84c`）
- 其他卡片左边框 3px，颜色对应数据状态
- 通过/未通过/无法计算用绿/红/灰小圆点 + 数字
- 评分 0 且已计算：白色 `0.0`（不标黄标红）
- 评分为 null/undefined：灰色 `—`

### 5.2 评分公式可视化

```
评分公式:  50 + 放量突破(+8.0) + 热点匹配(+15.0) = 73.0

明细:
  基础分             50.0   (技术面 0% + 宏观面 100%)
  ─────────────────────────────────────
  + 放量突破          +8.0   (规则: today_volume_exceeds_prior_3_max)
  + 热点匹配         +15.0   (规则: company_event_hot_sector_link)
  ─────────────────────────────────────
  最终得分           73.0
```

### 5.3 规则明细表（增强版）

替代现有的 4 列简化表：

| 规则 | 状态 | 类别 | 所需数据 | 实际值 | 权重 | 贡献 |
|------|------|------|---------|--------|------|------|
| zuoyi_signal | 🟢 通过 | technical | K线×15日 | 左一85.2 > 均价82.1 | 8 | +8.0 |
| ema_breakout | 🔴 未通过 | technical | K线×150日 | EMA10=78.3 < EMA150=80.1 | 5 | 0 |
| rsi_oversold | ⚪ 无法计算 | technical | RSI(14) | 数据不足(需14日,实3日) | 3 | — |
| daily_volume | 🟢 通过 | technical | 成交量×5日 | 今日2.1亿 > 前3日均1.2亿 | 6 | +6.0 |
| macro_factor | 🟢 通过 | macro | 宏观评分 | 评分=62/阈值=50 | 10 | +10.0 |
| hot_sector | 🟡 数据缺失 | macro | Tavily API | API 超时未返回 | 5 | — |

每行支持：
- **展开详情**：点击行展开，显示 `reason` 完整文本和 `details` JSON
- **所需字段 tooltip**：hover 显示后端需要的具体数据源
- **分组表头**：按 `strategy_category` 分组，每组有汇总计数

### 5.4 规则分组卡片布局

```tsx
function RuleDetailGroup({ category, rules, defaultExpanded = true }) {
  const stats = useMemo(() => ({
    pass: rules.filter(r => r.result === 'pass').length,
    fail: rules.filter(r => r.result === 'fail').length,
    unknown: rules.filter(r => r.result === 'unknown' || r.result === 'not_executed').length,
    missing: rules.filter(r => r.status === 'missing').length,
  }), [rules])

  return (
    <details open={defaultExpanded} className="rule-group-card">
      <summary className="rule-group-header">
        <h3>
          <span className="gold-dot" />
          {categoryLabel(category)}
        </h3>
        <span className="rule-group-stats">
          <span className="stat-pass">{stats.pass} 通过</span>
          <span className="stat-fail">{stats.fail} 未通过</span>
          {stats.unknown > 0 && <span className="stat-unknown">{stats.unknown} 无法计算</span>}
          {stats.missing > 0 && <span className="stat-missing">{stats.missing} 数据缺失</span>}
        </span>
      </summary>
      <table className="rule-detail-table">
        <thead>
          <tr>
            <th>规则</th>
            <th>状态</th>
            <th>所需数据</th>
            <th>实际值</th>
            <th>权重</th>
            <th>贡献</th>
          </tr>
        </thead>
        <tbody>
          {rules.map(rule => <RuleDetailRow key={rule.rule_key} rule={rule} />)}
        </tbody>
      </table>
    </details>
  )
}
```

---

## 六、历史记录优化

### 6.1 增强的历史记录表格

| 时间 | 股票 | 市场 | 周期 | 规则链 | 结果 | 综合分 | 失败原因 | 操作 |
|------|------|------|------|--------|------|--------|---------|------|
| 06-12 14:30 | 腾讯 | HK | 1d | 统一看涨 | 🟢 | 73.0 | — | 🔄 📋 📊 |
| 06-10 09:15 | 腾讯 | HK | 1d | 统一看涨 | 🔴 | — | K线数据不足 | 🔄 📋 |
| 06-08 16:00 | 小米 | HK | 1wk | EMA突破 | 🟢 | 58.5 | — | 🔄 📋 |

### 6.2 操作按钮定义

| 按钮 | 行为 | 实现 |
|------|------|------|
| 🔄 **重新筛选** | 将历史记录的参数（股票/市场/周期/规则链）回填到表单，自动触发筛选 | `selectStock()` + `setChainKey()` + `submit()` |
| 📋 **复制参数** | 复制 JSON 格式参数到剪贴板 | `navigator.clipboard.writeText(JSON.stringify({market, code, timeframe, chain_key}))` |
| 📊 **对比分数** | 选择两条历史记录，弹出分数变化对比面板 | `ScoreComparisonModal` |

### 6.3 分数对比弹窗

```
┌─ 分数变化对比 ─────────────────────────────┐
│ 腾讯控股 (HK.00700)  1d / 统一看涨Top20      │
│                                            │
│ 指标        │ 06-10 │ 06-12 │ 变化         │
│ ─────────── ┼───────┼───────┼──────        │
│ 综合评分     │ 68.0  │ 73.0  │ +5.0 ▲      │
│ 技术维度     │ 48.0  │ 50.0  │ +2.0 ▲      │
│ 宏观维度     │ 15.0  │ 18.0  │ +3.0 ▲      │
│ 资金风险     │  5.0  │  5.0  │  0  —       │
│                                            │
│ 通过规则变化: +2 (volume_breakout, hot_link)│
│ 无法计算→通过: +1 (macro_factor)            │
└────────────────────────────────────────────┘
```

---

## 七、K线数据链路问题诊断与修复 🔴

> **紧急程度：阻塞性** — K 线图表完全不展示数据，用户无法看到任何价格走势。  
> 以下为端到端链路追踪后的完整诊断。

### 7.1 当前数据流架构

```
┌─ Frontend ─────────────────────────────────────────────────────────┐
│ fetchKline(code)                                                    │
│   GET /api/stock-terminal/{market}/{code}/klines                    │
│        ?timeframe=1d&limit=200                                      │
│   ↓ 仅检查 data.rows，忽略 source_status ⚠️                         │
└────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─ Backend Router: web/stock_terminal.py ────────────────────────────┐
│ normalize_stock_code("HK", "HK.00700") → "HK.00700"                │
│   ↓                                                                 │
│ StockTerminalService.get_klines("HK", "HK.00700", "1d", 200)       │
└────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─ Step 1: MySqlStockTerminalRepository.get_klines() ────────────────┐
│   db.get_kline_cache(market="HK", code="HK.00700", timeframe="1d") │
│   → SELECT FROM stock_kline_cache WHERE market='HK' AND            │
│     code='HK.00700' AND timeframe='1d' ORDER BY bar_time DESC       │
│                                                                     │
│   ├─ HIT  → return rows, status="cached" ✅ 直接返回                │
│   └─ MISS → return [], status="empty"  → 进入 Step 2               │
└────────────────────────────────────────────────────────────────────┘
        │ (cache miss)
        ▼
┌─ Step 2: EastmoneyStockTerminalProvider.fetch_klines() ────────────┐
│                                                                     │
│   A) _fetch_klines_from_eastmoney()                                 │
│      GET push2his.eastmoney.com/api/qt/stock/kline/get              │
│        ?secid=116.00700&klt=101&fqt=1&lmt=200&end=20500101          │
│                                                                     │
│      ├─ ✅ SUCCESS → return List[KlinePoint]                        │
│      └─ ❌ ANY ERROR → except Exception: pass → returns [] 🔴 BUG 1 │
│                                                                     │
│   B) KlineFetcherFactory.create_fetcher_chain(db=db)                │
│      ├─ DatabaseKlineFetcher ─→ db.get_kline_cache() (重复查询!)    │
│      ├─ YFinanceKlineFetcher ─→ yf.download("0700.HK", ...)        │
│      └─ AKShareKlineFetcher  ─→ ak.stock_hk_daily("00700", ...)    │
│                                                                     │
│   └─ ALL FAIL → raise RuntimeError("...")                           │
└────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─ Response to Frontend ─────────────────────────────────────────────┐
│ {                                                                    │
│   "rows": [],                     ← 前端只检查这个!                  │
│   "source_status": {                                                 │
│     "kline": {                                                       │
│       "status": "error",          ← 被忽略! 🔴 BUG 2                │
│       "source": "",                                                 │
│       "error_message": "eastmoney kline provider returned no data"   │
│     }                                                                │
│   },                                                                 │
│   "data_gaps": ["kline"]          ← 被忽略! 🔴 BUG 2                │
│ }                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

### 7.2 根因分析

#### 🔴 Bug 1（关键）— Eastmoney API 错误被静默吞没

**文件**：`stock_terminal/providers/eastmoney.py:90-91`

```python
# 当前代码
except Exception:
    pass  # ← 所有异常（网络超时/DNS解析失败/HTTP 403/响应格式错误）被无声丢弃!
return []
```

**影响**：Eastmoney API 一旦出错（网络不通、IP 限频、返回格式变化），错误信息完全丢失。上层调用方只能看到"returned no data"，无法区分"API 挂了"还是"该股票确实无数据"。

**典型失败场景**：
- Eastmoney `push2his` 接口可能要求特定 User-Agent 或 Referer
- 云服务器 IP 被 Eastmoney 风控系统限频/封禁
- `secid` 格式 `116.XXXXX` 对部分港股可能不适用
- 网络超时（`self.timeout_sec=5.0`）

#### 🔴 Bug 2（关键）— 前端丢弃后端返回的错误诊断

**文件**：`web_frontend/src/main.tsx:948-956`

```tsx
// 当前代码 — 仅取 rows，忽略所有诊断信息
const data = await api<{ rows: Array<Record<string, unknown>> }>(
  `/api/stock-terminal/${...}/klines?timeframe=${...}&limit=200`
)
setKlineRows(Array.isArray(data.rows) ? data.rows : [])
// ← source_status、data_gaps 完全未使用!
```

**影响**：后端在 `_series_error_payload()` 中已经构造了完整错误报告（`status="error"` + `error_message` + `data_gaps`），但前端全部丢弃。用户在空图表上只能看到"暂无K线数据"，不知道是数据源故障还是股票不支持。

#### 🟡 Bug 3（中）— 同一条 DB 缓存被查两次

```
Step 1: repository.get_klines() → db.get_kline_cache("HK", "HK.00700", "1d") → MISS
Step 2B: DatabaseKlineFetcher → db.get_kline_cache("HK", "HK.00700", "1d") → MISS (again!)
```

`stock_kline_cache` 对同一 `(market, code, timeframe)` 组合执行了两次相同的 SELECT。第一次在 `MySqlStockTerminalRepository`，第二次在 `DatabaseKlineFetcher`（通过 `KlineFetcherFactory` 链）。

#### 🟡 Bug 4（中）— 两条 K 线数据路径分叉，架构冗余

| 路径 | 用途 | 入口 | 缓存 |
|------|------|------|------|
| **Display** | K 线图表渲染 | `GET /api/stock-terminal/{m}/{c}/klines` | `stock_kline_cache` (TTL 缓存) |
| **Analysis** | 筛选规则评估 | `_fetch_single_kline()` → `KlineFetcherFactory` | 无缓存，每次实时拉取 |

Display 路径有 MySQL TTL 缓存，Analysis 路径没有。Analysis 每次筛选都调 YFinance/AKShare，可能触发限频。两条路径应统一。

#### 🟡 Bug 5（低）— Eastmoney 请求零日志

`_fetch_klines_from_eastmoney` 中无任何 `print`/`logger` 输出。排查时必须改代码才能看到 API 请求 URL、响应状态码、响应体大小。

### 7.3 修复方案

#### P0-6：修复 Eastmoney 静默吞错 + 添加请求日志

**文件**：`stock_terminal/providers/eastmoney.py`

核心改动：将 `except Exception: pass` 改为返回结构化错误，同时给 `fetch_klines()` 补充 per-provider 错误收集。

```python
def _fetch_klines_from_eastmoney(self, market, code, timeframe, limit):
    """返回 (rows, error_message)。rows 非空时 error_message 为 None"""
    import sys
    klt = self._KLT_MAP.get(timeframe)
    if klt is None:
        return [], f"unsupported timeframe: {timeframe}"

    secid = self._secid(market, code)
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": klt, "fqt": 1,
        "lmt": max(1, min(int(limit), 500)),
        "end": "20500101",
    }

    try:
        response = self.session.get(url, params=params, timeout=self.timeout_sec)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        msg = f"Eastmoney HTTP failed: secid={secid} error={exc}"
        print(f"[eastmoney] {msg}", file=sys.stderr)
        return [], msg  # ← 不再吞咽!

    klines = (data.get("data") or {}).get("klines") or []
    if not klines:
        rc = (data.get("data") or {}).get("rc", -1)
        msg = f"Eastmoney returned empty: secid={secid} rc={rc}"
        print(f"[eastmoney] {msg}", file=sys.stderr)
        return [], msg

    rows = []
    for text in klines:
        parts = str(text).split(",")
        if len(parts) < 7:
            continue
        rows.append(KlinePoint(
            at=_parse_dt(parts[0]),
            open=float(parts[1]), close=float(parts[2]),
            high=float(parts[3]), low=float(parts[4]),
            volume=float(parts[5]),
            turnover=float(parts[6]) if len(parts) > 6 else 0.0,
        ))

    if rows:
        self.last_source = "eastmoney"
        print(f"[eastmoney] OK: {len(rows)} bars secid={secid} timeframe={timeframe}", file=sys.stderr)
        return rows, None  # None = no error

    return [], f"Eastmoney returned 0 parseable bars: secid={secid}"
```

同步修改 `fetch_klines()` 收集各 fallback 层级的错误：

```python
def fetch_klines(self, market, code, timeframe, limit):
    errors = []

    # 1. Try Eastmoney direct API
    rows, err = self._fetch_klines_from_eastmoney(market, code, timeframe, limit)
    if rows:
        return rows
    if err:
        errors.append(f"eastmoney: {err}")

    # 2. Fallback: fetcher chain
    from kline_fetcher import KlineFetcherFactory
    fetchers = KlineFetcherFactory.create_fetcher_chain(db=None)  # skip DB cache
    for fetcher in fetchers:
        source = fetcher.get_name()
        try:
            frame = fetcher.fetch(code, market=market, timeframe=timeframe, max_count=limit)
        except Exception as exc:
            errors.append(f"{source}: {exc}")
            continue
        rows = self._rows_from_frame(frame, limit)
        if rows:
            self.last_source = source
            return rows
        else:
            errors.append(f"{source}: returned empty DataFrame")

    raise RuntimeError("; ".join(errors) if errors else "no K-line data available")
```

#### P0-7：前端读取 K 线 source_status 并展示诊断

**文件**：`web_frontend/src/main.tsx` — `fetchKline()` 函数

```tsx
// 新增状态
const [klineDiagnostics, setKlineDiagnostics] = useState<{
  status: string; source: string; error_message?: string
} | null>(null)

async function fetchKline(code: string) {
  setKlineLoading(true)
  setKlineError('')
  setKlineDiagnostics(null)
  try {
    const data = await api<{
      rows: Array<Record<string, unknown>>
      source_status?: { kline?: { status: string; source: string; error_message?: string } }
      data_gaps?: string[]
    }>(`/api/stock-terminal/${encodeURIComponent(market)}/${encodeURIComponent(code)}/klines?timeframe=${encodeURIComponent(timeframe)}&limit=200`)

    setKlineRows(Array.isArray(data.rows) ? data.rows : [])

    // ✅ 保存 K 线数据源诊断
    const klineStat = data.source_status?.kline
    if (klineStat) {
      setKlineDiagnostics({
        status: klineStat.status || 'unknown',
        source: klineStat.source || '',
        error_message: klineStat.error_message || '',
      })
      // 如果后端标记为 error 或 data_gaps 含 kline，设置错误信息
      if (klineStat.status === 'error' || (data.data_gaps || []).includes('kline')) {
        setKlineError(klineStat.error_message || 'K线数据获取失败')
      }
    }
  } catch (err) {
    setKlineError(err instanceof Error ? err.message : '加载K线失败')
    setKlineRows([])
  } finally { setKlineLoading(false) }
}
```

**文件**：`KlineChart.tsx` — 增强空状态

```tsx
interface Props {
  // ...existing
  diagnostics?: { status: string; source: string; error_message?: string } | null
}

// 替换原有空状态
{!loading && !error && rows.length === 0 && (
  <div className="chart-empty-overlay">
    <div className="empty-state">
      <span className="empty-icon">📊</span>
      <span className="empty-title">暂无K线数据</span>
      {diagnostics?.error_message ? (
        <span className="empty-detail text-danger">
          {diagnostics.error_message}
        </span>
      ) : (
        <span className="empty-detail">
          该股票在当前周期下暂无可用K线数据
        </span>
      )}
      <div className="empty-actions">
        <span className="empty-hint">建议：切换至更长周期（周线/月线），或检查股票代码</span>
      </div>
    </div>
  </div>
)}
```

#### P0-8：消除 DB 缓存重复查询

**文件**：`kline_fetcher.py` — `KlineFetcherFactory`

新增 `skip_db_cache` 参数：

```python
@staticmethod
def create_fetcher_chain(quote_ctx=None, db=None, rate_limiter=None, skip_db_cache=False):
    fetchers = []

    # 1. Database cache（可跳过，当调用方已自行查过时）
    if db is not None and not skip_db_cache:
        try:
            fetchers.append(DatabaseKlineFetcher(db))
        except Exception:
            pass

    # 2. Futu, YFinance, AKShare ... (不变)
    ...
```

在 `eastmoney.py` 的 `fetch_klines()` 中调用时传入 `skip_db_cache=True`：

```python
fetchers = KlineFetcherFactory.create_fetcher_chain(db=self.db, skip_db_cache=True)
```

### 7.4 K 线数据就绪检查（附加优化）

在 `POST /api/screening/single-stock` 响应中增加 K 线就绪字段，让前端在提交筛选前即可判断：

```json
{
  "run_id": "abc123",
  "kline_available": true,
  "kline_source": "eastmoney",
  "kline_bars": 200
}
```

前端据此：
- `kline_available === false` → 显示黄色提示"该股票暂无K线缓存，图表可能为空，是否继续？"
- 避免用户等待后才看到空图表

### 7.5 新增数据源：富途 OpenD 直连 🔵

> **富途 OpenD** 是富途牛牛提供的本地 API 网关，支持获取港股/美股/A股的实时行情和历史 K 线，数据质量远高于 Eastmoney/YFinance/AKShare 等免费源。

#### 7.5.1 当前架构中 OpenD 的使用现状

```
┌─ 用户本地机器 ───────────────────────────────────────────┐
│                                                          │
│  Futu OpenD (127.0.0.1:11111)                            │
│      ▲                                                   │
│      │ TCP (ft.OpenQuoteContext)                         │
│      │                                                   │
│  local_agent.py ──── push_klines() ────▶ Cloud MySQL     │
│  (FutuKlineFetcher)                 stock_kline_cache    │
│                                                          │
│  desktop.py (pywebview)                                  │
│      │                                                   │
│      │ 启动 FastAPI (localhost)                           │
│      │     → 走 Eastmoney/YFinance fallback               │
│      │     → OpenD 未连接 ⚠️                              │
│                                                          │
│  interactive_screening.py                                │
│      │                                                   │
│      │ FutuKlineFetcher(quote_ctx) ✅ 已连接              │
│      │     → 占 KlineFetcherFactory 优先级 #2            │
│                                                          │
└──────────────────────────────────────────────────────────┘

┌─ 云端服务器 ─────────────────────────────────────────────┐
│                                                          │
│  FastAPI Web Backend                                     │
│      │                                                   │
│      │ StockTerminalService                              │
│      │     → EastmoneyStockTerminalProvider              │
│      │         → KlineFetcherFactory(db=db)              │
│      │             → DatabaseKlineCache (Agent推送的缓存) │
│      │             → YFinance                             │
│      │             → AKShare                              │
│      │             → FutuKlineFetcher? ❌ 未连接          │
│                                                          │
│  MySQL: stock_kline_cache (Agent 推送的唯一入口)          │
└──────────────────────────────────────────────────────────┘
```

**现状总结**：

| 运行模式 | OpenD 可访问? | 当前是否使用? | 原因 |
|---------|-------------|-------------|------|
| `interactive_screening.py` | ✅ 本地 | ✅ 直接连接 | 显式传入 `quote_ctx` |
| `local_agent.py` | ✅ 本地 | ✅ 直接连接 | 显式传入 `quote_ctx` |
| `desktop.py` (pywebview) | ✅ 本地 | ❌ 未连接 | Web backend 走 API fallback 路径 |
| Web 云端部署 | ❌ 不可达 | ❌ 无法连接 | 网络隔离 |
| Docker/自部署 | ✅ 可达（同网络） | ❌ 未连接 | 缺少 env var 配置 |

#### 7.5.2 改造方案：KlineFetcherFactory 支持 env var 自动连接 OpenD

**核心思路**：让 `KlineFetcherFactory.create_fetcher_chain()` 在 `quote_ctx` 未显式传入时，尝试从环境变量创建 OpenD 连接。

**改动文件**：`kline_fetcher.py` — `KlineFetcherFactory.create_fetcher_chain()`

```python
@staticmethod
def create_fetcher_chain(
    quote_ctx=None,
    db=None,
    rate_limiter=None,
    skip_db_cache=False,
) -> List[KlineFetcherBase]:
    fetchers: List[KlineFetcherBase] = []
    disable_akshare = KlineFetcherFactory._env_enabled("KLINE_DISABLE_AKSHARE", default=False)

    # 1. Database cache
    if db is not None and not skip_db_cache:
        try:
            fetchers.append(DatabaseKlineFetcher(db))
        except Exception:
            pass

    # 2. Futu OpenD（如果 env var 配置了连接信息）
    futu_host = os.getenv("FUTU_OPEN_HOST", "").strip()
    futu_port = int(os.getenv("FUTU_OPEN_PORT", "11111") or "11111")

    # 方式 A：调用方显式传入 quote_ctx（现有逻辑，不变）
    if quote_ctx is not None:
        try:
            fetchers.append(FutuKlineFetcher(quote_ctx, rate_limiter))
        except Exception:
            pass

    # 方式 B（新增）：通过环境变量自动连接 OpenD
    elif futu_host:
        try:
            import futu as ft
            auto_ctx = ft.OpenQuoteContext(host=futu_host, port=futu_port)
            # 将 ctx 的生命周期绑定到工厂？不——每个请求独立创建/销毁
            # 策略：创建一个轻量 wrapper，每次 fetch 时尝试连接
            fetchers.append(OpenDQuotedKlineFetcher(
                host=futu_host, port=futu_port, rate_limiter=rate_limiter
            ))
        except ImportError:
            pass  # futu SDK 未安装
        except Exception:
            pass  # 连接失败，静默跳过

    # 3. YFinance（全 timeframe）
    try:
        fetchers.append(YFinanceKlineFetcher())
    except ImportError:
        pass

    # 4. AKShare
    if not disable_akshare:
        try:
            fetchers.append(AKShareKlineFetcher())
        except ImportError:
            pass

    return fetchers
```

**新增类**：`OpenDQuotedKlineFetcher` — 每次 fetch 时创建临时连接

```python
class OpenDQuotedKlineFetcher(KlineFetcherBase):
    """通过 env var 自动连接 Futu OpenD 的 K 线获取器。
    
    与 FutuKlineFetcher 不同，此类自行管理 OpenD 连接生命周期：
    每次 fetch 时创建临时 quote_ctx，用完即释放。
    适用于 web 后端场景（多请求并发，每个请求独立连接）。
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 11111, rate_limiter=None):
        self.host = host
        self.port = port
        self.rate_limiter = rate_limiter

    def get_name(self) -> str:
        return f"FutuOpenD({self.host}:{self.port})"

    def fetch(self, stock_code, market="HK", timeframe="1d", max_count=2000):
        import futu as ft
        quote_ctx = None
        try:
            quote_ctx = ft.OpenQuoteContext(host=self.host, port=self.port)
            inner = FutuKlineFetcher(quote_ctx, self.rate_limiter)
            return inner.fetch(stock_code, market, timeframe, max_count)
        except Exception as e:
            _log_fetch_warning(f"FutuOpenD({self.host}:{self.port})", "fetch", e)
            return None
        finally:
            if quote_ctx is not None:
                try:
                    quote_ctx.close()
                except Exception:
                    pass
```

#### 7.5.3 部署场景分析

| 场景 | FUTU_OPEN_HOST | 效果 |
|------|---------------|------|
| **Desktop 模式** | `127.0.0.1` (默认) | ✅ K线图表直接从 OpenD 获取，数据最新最全 |
| **Docker 同网络** | `192.168.1.100` | ✅ Web 后端通过 TCP 访问同网络 OpenD |
| **云端部署** | 不设置 | 保持现有行为：DB缓存 → YFinance → AKShare |
| **Agent + Web 分离** | 不设置 | Agent 定时推送缓存，Web 读缓存 |

**优先级配置**（Futu 接入后的完整 fallback 链）：

```
K线获取优先级：
  1. stock_kline_cache (MySQL)         ← Agent 推送的 OpenD 数据
  2. Futu OpenD 直连 (env var 配置)    ← 实时数据，质量最高 🆕
  3. Eastmoney 直连 API                ← 免费，支持全 timeframe
  4. YFinance                          ← 免费，全球覆盖
  5. AKShare                           ← 免费，A股分钟线
```

#### 7.5.4 安全注意事项

- OpenD 默认监听 `127.0.0.1`，仅本地可访问
- 如需远程访问，应通过 SSH tunnel 或 VPN，**不要**将 OpenD 直接暴露在公网
- `FUTU_OPEN_HOST` 应默认不设置，由部署者显式配置
- 每次 fetch 后立即 `close()` 连接，不保持长连接（避免 OpenD 连接池耗尽）

#### 7.5.5 对前端的价值

接入 Futu OpenD 后，K 线图表的 **数据覆盖率** 和 **数据质量** 将显著提升：

| 指标 | 当前（仅 Eastmoney/YFinance） | 接入 OpenD 后 |
|------|---------------------------|-------------|
| 港股 K 线覆盖率 | ~60%（YFinance 限频严重） | ~99%（OpenD 直连富途） |
| 美股 K 线覆盖率 | ~70% | ~99% |
| A 股 K 线覆盖率 | ~90%（AKShare 兜底） | ~99% |
| 分钟线支持 | 仅 A 股（AKShare） | HK/US/A 全市场 |
| 数据延迟 | 日线 T+1 | 日线 T+0（实时收盘） |

---

## 八、开发实现优先级（更新）

### P0 — 核心体验修复（必须立即解决）

| # | 任务 | 影响文件 | 预估 |
|---|------|---------|------|
| P0-1 | **数据状态区分**：新增 `DataStatusBadge`，区分"已计算0分/未计算/数据缺失/接口失败" | `main.tsx`, `styles.css` | 2h |
| P0-2 | **评分卡片重设计**：4 张评分卡片（综合/技术/宏观/事件热度），显示通过/未通过/无法计算计数 | `main.tsx`, `styles.css` | 3h |
| P0-3 | **数据诊断模块**：6 项诊断检查点 + 后端状态透传 | `main.tsx`, `main.py` | 4h |
| P0-4 | **失败原因展示**：后端返回 `error_detail` + `failed_step` + `suggestions` | `main.py`, `main.tsx` | 3h |
| P0-5 | **K线空状态/错误状态**：区分"无数据/加载中/接口失败"，骨架屏替换空白 | `KlineChart.tsx` | 2h |
| **P0-6** 🔴 | **修复 Eastmoney 静默吞错**：`except Exception: pass` → 返回结构化错误 + 添加请求日志 | `eastmoney.py` | 1.5h |
| **P0-7** 🔴 | **前端读取 K 线 source_status**：读取诊断字段并在 KlineChart 空状态中展示数据源错误 | `main.tsx`, `KlineChart.tsx` | 1.5h |
| **P0-8** 🟡 | **消除 DB 缓存重复查询**：`KlineFetcherFactory` 支持 `skip_db_cache`，避免同一次请求查两次 | `eastmoney.py`, `kline_fetcher.py` | 0.5h |
| **P0-9** 🔵 | **接入富途 OpenD 作为 K 线数据源**：`KlineFetcherFactory` 支持 `FUTU_OPEN_HOST`/`FUTU_OPEN_PORT` env var 自动连接，新增 `OpenDQuotedKlineFetcher` 每次 fetch 时创建/释放临时连接 | `kline_fetcher.py` | 2h |

### P1 — 交互流程增强

| # | 任务 | 影响文件 | 预估 |
|---|------|---------|------|
| P1-1 | **规则明细表增强**：6 列展示（规则/状态/所需数据/实际值/权重/贡献），按 strategy_category 分组 | `main.tsx`, `styles.css` | 4h |
| P1-2 | **失败恢复操作**：ErrorRecoveryCard 含重试/诊断/切换周期/仅技术面按钮 | `main.tsx` | 2h |
| P1-3 | **历史记录增强**：失败原因列 + 重新筛选/复制参数操作按钮 | `main.tsx` | 3h |
| P1-4 | **数据来源映射**：`web_backend` → `Web 后端直连`，`yfinance` → `Yahoo Finance` | `main.tsx` | 0.5h |
| P1-5 | **评分公式可视化**：展示 `50 + 放量(+8) + 热点(+15) = 73.0` 的完整拆解 | `main.tsx` | 2h |

### P2 — 体验打磨

| # | 任务 | 影响文件 | 预估 |
|---|------|---------|------|
| P2-1 | **长文本溢出**：板块 tooltip、规则名换行、原因展开/收起、代码复制 | 多组件 | 3h |
| P2-2 | **股票搜索多候选**：键盘导航 + 市场标签 + 候选数限制提示 | `main.tsx` | 2h |
| P2-3 | **历史分数对比**：两条记录的并排分数变化 + 规则变化 | 新建组件 | 3h |
| P2-4 | **K线骨架屏动画**：shimmer 效果的模拟 K 线柱 | `KlineChart.tsx`, `styles.css` | 1.5h |
| P2-5 | **按钮交互动画**：hover glow / loading spinner / disabled 过渡 | `styles.css` | 1h |

---

## 九、React 组件结构建议

当前问题：`CodeScreening` 是一个约 470 行的单体组件，所有逻辑（表单、搜索、K线、进度、报告、历史）耦合在一起。

### 9.1 推荐目录结构

```
src/features/codeScreening/
├── CodeScreeningPage.tsx            # 页面容器（约 80 行）
├── components/
│   ├── ScreeningForm.tsx            # 筛选表单（市场/周期/规则链 + 搜索）
│   ├── StockSearchInput.tsx         # 股票搜索 + 候选下拉 + 键盘导航
│   ├── StockInfoCard.tsx            # 股票信息确认卡片
│   ├── DataDiagnosticPanel.tsx      # 6 项数据诊断面板
│   ├── ScoreOverviewCards.tsx       # 4 张评分概览卡片
│   ├── ScoreFormulaDisplay.tsx      # 评分公式拆解
│   ├── RuleDetailTable.tsx          # 增强规则明细表
│   ├── RuleGroupCard.tsx            # 规则分组折叠卡片
│   ├── ErrorRecoveryCard.tsx        # 失败恢复操作卡片
│   ├── ScreeningProgress.tsx        # 进度条 + 步骤指示器
│   ├── KlineSection.tsx             # K线区域（含骨架屏/空状态/错误）
│   ├── ReportPanel.tsx              # 报告面板（Tab: 本次/历史）
│   ├── HistoryTable.tsx             # 增强历史记录表
│   └── ScoreComparisonModal.tsx     # 分数对比弹窗
├── hooks/
│   ├── useStockSearch.ts            # 股票搜索 + debounce
│   ├── useScreeningProgress.ts      # SSE 进度监听
│   ├── useScreeningSubmit.ts        # 筛选提交 + K线获取
│   └── useScreeningHistory.ts       # 历史记录加载
├── utils/
│   ├── dataStatus.ts                # 数据状态判定（computed/not_computed/missing/error）
│   ├── dataSourceLabels.ts          # data_source → 中文映射
│   └── formatRuleDetail.ts          # 规则明细格式化
└── types.ts                         # CodeScreening 专用类型
```

### 9.2 关键类型定义

```typescript
// types.ts

/** 数据完整性状态 */
type DataStatus = 'computed' | 'not_computed' | 'missing' | 'error'

/** 规则执行结果 */
type RuleResult = 'pass' | 'fail' | 'unknown' | 'not_executed'

/** 数据诊断项 */
interface DiagnosticItem {
  key: string
  label: string
  status: DataStatus
  detail: string
  suggestion?: string
}

/** 增强的规则明细 */
interface EnhancedRuleDetail {
  rule_key: string
  rule_name: string
  strategy_category: string       // 'technical' | 'macro'
  result: RuleResult
  required_fields: string[]       // ['K线日线×15', '成交量']
  actual_data: string             // '左一价=85.2 > 均价82.1'
  weight: number
  score_impact: number            // 对最终评分的贡献值
  reason: string
  data_status: DataStatus         // 该规则的数据完整性
  details?: Record<string, unknown>
  display_order: number
}

/** 错误恢复选项 */
interface ErrorRecovery {
  error_type: string
  error_detail: string
  failed_step: string
  suggestions: Array<{
    action: 'retry' | 'view_diagnostic' | 'switch_timeframe' | 'tech_only' | 'copy_error'
    label: string
    params?: Record<string, string>
  }>
}

/** 评分维度 */
interface ScoreDimension {
  label: string                    // '技术维度'
  score: number | null
  weight_pct: number               // 40
  pass_count: number
  fail_count: number
  unknown_count: number
  status: DataStatus
}

/** 增强的筛选结果 */
interface EnhancedScreeningResult {
  // 原有字段...
  diagnostics: DiagnosticItem[]
  rule_details: EnhancedRuleDetail[]
  error_recovery?: ErrorRecovery
  score_formula: string            // '50 + 放量(+8) + 热点(+15) = 73.0'
  score_breakdown: Array<{
    label: string
    value: number
    description: string
  }>
  dimensions: ScoreDimension[]
  data_source_label: string        // 'Yahoo Finance + 富途 OpenD'
}
```

### 9.3 数据流图

```
用户输入
  │
  ├─ StockSearchInput ──(debounce 300ms)──▶ GET /api/stocks/search
  │       │
  │       └── 选择股票 ──▶ StockInfoCard
  │
  └─ [开始筛选] ──▶ useScreeningSubmit
                      │
                      ├── fetchKline() ──(并行)──▶ GET /api/stock-terminal/{m}/{c}/klines
                      │
                      └── POST /api/screening/single-stock ──▶ runId
                              │
                              ▼
                      useScreeningProgress (SSE)
                              │
                              ├── 更新进度条 + 步骤指示器
                              ├── 更新数据诊断面板
                              └── completed/failed
                                      │
                                      ▼
                              GET /api/screening/single-stock/{runId}
                                      │
                                      ├── 成功 ──▶ ScoreOverviewCards
                                      │           ScoreFormulaDisplay
                                      │           RuleGroupCard × N
                                      │
                                      └── 失败 ──▶ ErrorRecoveryCard
                                                  DataDiagnosticPanel

历史记录 ──▶ GET /api/screening/single-stock/history/{code}
                  │
                  ├── 🔄 重新筛选 ──▶ 回填表单 → submit()
                  └── 📊 对比分数 ──▶ ScoreComparisonModal
```

---

*文档版本: v1.0*  
*生成时间: 2026-06-12*  
*基于 MoneyManager 代码库实际分析*
