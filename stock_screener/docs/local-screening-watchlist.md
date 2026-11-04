# 本地个股测试名单

名单位于 `tests/fixtures/local_screening_watchlist.json`，当前只有 `HK.00700`。
可在 `codes` 中添加完整代码。仅用于本地个股报告流程验证，不是股票推荐。

正式规则引擎、数据库规则 DSL、启用状态和默认 SQL 均不变。普通启动方式不会
导入测试启动器，因此不会自动通过任何股票。测试启动器必须显式传入
`--enable-simulated-technicals`，并且只能监听 `127.0.0.1`。

从 `stock_screener` 目录启动独立测试 API：

```sh
source .env
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
- 报告模型输入注明技术条件被模拟，MySQL 报告标题、结构化正文也注明本地测试。
- 行情、搜索、LLM、数据库断点和 MySQL 报告存储仍走真实代码；错误不伪装成功。
- Redis、MySQL 和模型配置需要真实可用。此启动器不用于全市场或定时执行。

结束后停止这两个测试进程即可。正常页面仍为 5174，关闭测试进程不需要恢复
数据库规则、名单股票或生产环境配置。

## 报告搜索

报告列表、详情和中英文关键词搜索使用 MySQL 报告表。报告正文以结构化 JSON
保存，搜索结果的高亮片段由 API 生成并以纯文本返回。浏览器验收脚本通过
Playwright 拦截报告 API，不会写入或覆盖本地报告数据。
