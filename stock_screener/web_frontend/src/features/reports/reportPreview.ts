import type { ReportDocument, ReportList } from './ReportsPage'

// Development-only fixtures. Never persisted or used as an API failure fallback.
const examples = [
  { title: '腾讯控股｜宏观政策、公司信息与技术信号观察', market: ['HK'], names: ['腾讯控股'], codes: ['HK.00700'], type: 'single_stock',
    summary: '以腾讯控股为例，展示宏观政策背景、公司经营观察、技术依据与风险提示。技术条件为模拟通过，以下内容不对应实时行情。' },
  { title: '跨市场精选｜港股、美股与 A 股任务报告', market: ['HK', 'US', 'A'], names: ['腾讯控股', 'Apple', '贵州茅台'], codes: ['HK.00700', 'US.AAPL', 'SH.600519'], type: 'market',
    summary: '一次逻辑任务合并展示三个市场的背景与入选标的，保留各市场分析依据和信息缺失说明，不生成额外跨市场模型总结。' },
  { title: '美股科技观察｜公司事件与趋势跟踪', market: ['US'], names: ['Apple', 'Microsoft'], codes: ['US.AAPL', 'US.MSFT'], type: 'custom_list',
    summary: '自定义名单报告示例：结合公司事件、行业背景和技术条件，展示入选原因、主要风险与可追溯来源。' },
  { title: 'A 股日度筛选｜政策背景与技术共振', market: ['A'], names: ['贵州茅台', '宁德时代'], codes: ['SH.600519', 'SZ.300750'], type: 'scheduled',
    summary: '定时任务归档示例，集中查看当次入选标的及政策观察。所有数字与分析仅用于页面排版预览，不构成交易依据。' },
]

const reports: ReportDocument[] = Array.from({ length: 23 }, (_, index) => {
  const item = examples[index % examples.length]
  const taskId = `preview-task-${String(index + 1).padStart(3, '0')}`
  const day = 29 - Math.floor(index / 4)
  return {
    report_id: `${item.type}-${taskId}`, task_id: taskId, task_type: item.type,
    title: item.title + (index >= 4 ? ` · 归档示例 ${index + 1}` : ''),
    summary: item.summary, markets: item.market, names: item.names, codes: item.codes,
    passed_count: item.codes.length, generated_at: `2026-10-${day}T${String(8 - index % 4).padStart(2, '0')}:30:00+08:00`,
    task_date: `2026-10-${day}`, timeframe: '1d', chain_key: 'unified_bullish_top20',
    content_markdown: `> 本报告为 MOCK 示例。技术条件模拟通过，仅用于页面展示，不代表真实行情、公司事实或投资建议。

## 任务概况

本次示例任务使用 **统一看涨技术规则 Top20**，按逻辑任务归档。仅分析已入选股票，不遍历整个市场进行 AI 分析。

| 项目 | 内容 |
| --- | --- |
| 任务 ID | ${taskId} |
| 涉及市场 | ${item.market.join(' / ')} |
| 分析周期 | 日线 · 1d |
| 入选标的 | ${item.names.join('、')} |

## 市场背景与宏观政策

以下展示报告的阅读结构，不描述实际政策变化。正式报告会引用对应市场的宏观数据、政策信息及数据日期，并明确已发布数据与分析判断的区别。

- **政策观察：** 展示政策方向、事件日期及可能影响的业务环节。
- **宏观环境：** 结合通胀、国债收益率等已获取指标，缺失数据不补零。
- **传导路径：** 说明宏观变化如何影响需求、成本与估值，不将相关性直接当作因果。

## 入选股票分析

${item.names.map((name, i) => `### ${name} · ${item.codes[i]}

公司信息部分用于归纳可核验的经营事件、公告与业务观察；本示例不虚构具体财务数据。

| 分析维度 | 示例展示 | 说明 |
| --- | --- | --- |
| 技术条件 | 模拟通过 | 本地测试名单强制通过，不是实时技术信号 |
| 公司事件 | 待核验 | 正式报告附公告或新闻来源 |
| 宏观影响 | 观察项 | 需结合业务暴露判断，不构成确定结论 |

**跟踪要点：** 关注基本面验证与信号持续性，避免将单一技术条件解释为确定收益。`).join('\n\n')}

## 风险与信息缺失

1. 技术条件经过模拟，不能据此作出买卖决策。
2. 示例不包含实时行情、真实估值或未经核实的公司消息。
3. 正式分析仍可能受到数据延迟、供应商缺失和模型判断偏差影响。

## 信息来源

以下为来源入口示例，并非上述示例内容的事实引用。

- [香港交易所披露易](https://www.hkexnews.hk/)
- [美国 SEC 公告查询](https://www.sec.gov/edgar/search/)

---

*报告按任务存档；关键词查询与详情读取不调用 LLM。*`,
  }
})

export function previewReports(q: string, page: number): ReportList {
  const keyword = q.trim().toLowerCase()
  const selected = reports.filter(report => [report.title, report.summary, report.task_id, ...report.codes, ...report.names, report.content_markdown || ''].join(' ').toLowerCase().includes(keyword))
  return { reports: selected.slice((page - 1) * 20, page * 20).map(({ content_markdown: _, ...report }) => report),
    total: selected.length, page, page_size: 20 }
}

export function previewReport(id: string): ReportDocument {
  const report = reports.find(item => item.report_id === id)
  if (!report) throw new Error('示例报告不存在，请返回列表重新选择')
  return report
}
