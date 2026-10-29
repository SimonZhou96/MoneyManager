# 本地个股测试名单

名单位于 `tests/fixtures/local_screening_watchlist.json`，当前只有 `HK.00700`。
可在 `codes` 中添加完整代码。仅用于本地个股报告流程验证，不是股票推荐。

正式规则引擎、数据库规则 DSL、启用状态和默认 SQL 均不变。普通启动方式不会
导入测试启动器，因此不会自动通过任何股票。测试启动器必须显式传入
`--enable-simulated-technicals`，并且只能监听 `127.0.0.1`。

从 `stock_screener` 目录启动独立测试 API：

```sh
source .runtime_home/report-services/app.env
WEB_COOKIE_SECURE=0 PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python scripts/run_local_screening_test.py --enable-simulated-technicals
```

前端在另一个终端启动，避免与正常测试端口 5174 混淆：

```sh
cd web_frontend
VITE_BACKEND_URL=http://127.0.0.1:8002 npm run dev -- --host 127.0.0.1 --port 5175 --strictPort
```

在 `http://127.0.0.1:5175/` 选择 `HK.00700` 和
`unified_bullish_top20`，再提交一次个股筛选。

行为边界：

- 仅对名单内、已启用、`strategy_category=technical` 的策略返回模拟通过。
- 不伪造 RSI、均线或行情数值；规则原因和明细带有本地模拟标识。
- 名单外股票、宏观/市场策略、基本面过滤器及禁用规则保持正常行为。
- 仅影响测试 API 当前进程，不向普通 API、RQ worker 或部署配置传播补丁。
- 报告模型输入注明技术条件被模拟，ES 报告标题、摘要、正文也注明本地测试。
- 行情、搜索、LLM、数据库断点、ES 鉴权/持久化/查询仍走真实代码；错误不伪装成功。
- Redis/ES 和模型配置需要真实可用。此启动器不用于全市场或定时执行。

结束后停止这两个测试进程即可。正常页面仍为 5174，关闭测试进程不需要恢复
数据库规则、名单股票或生产环境配置。

## 本地 ES 搜索高亮

`http://127.0.0.1:5175/?page=reports&q=腾讯` 走真实 ES 查询。
旧的前端 `mock_reports` 开关已移除，不再依赖演示报告文件。
当前本地 ES 有 23 篇 `【本地搜索示例】` 报告，示例仅保留在本地 ES，
不创建真实筛选任务，也不调用行情、搜索供应商或 LLM。
报告数据、前端演示报告文件与浏览器截图/日志不提交；上述白名单 JSON 按要求提交。
没有本地示例数据的新环境会展示真实报告或空列表，不会回退假数据。

可用关键词：`腾讯`（标题/摘要/正文）、`人工智能`（仅正文）、
`HK.00700`（股票代码）、`preview-task-001`（任务 ID）。命中高亮由 ES 生成；
API 将其转为文字分段，前端使用 React 的 `<mark>` 展示，不插入未清洗 HTML。
当前高亮展示在搜索列表，详情仍展示原始报告正文并保留返回搜索条件。

导入工具 `scripts/seed_local_report_examples.py --confirm-local-es` 从标准输入
接收前端示例报告 JSON，只接受本地 HTTPS 9201 和 `preview-task-` 命名空间。
写入前检查所有 ID，使用 ES create 防止并发覆盖；重复导入跳过已有示例，
不会更新、删除或覆盖真实报告。
