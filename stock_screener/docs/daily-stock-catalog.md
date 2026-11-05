# 每日股票和 ETF 目录

独立 worker 位于 `worker/`，每天北京时间 00:00 同步，包含周末。首次启动或
休眠恢复后补跑当天未执行任务，不追补历史日期。它不执行筛选、行情分析、AI
或飞书发送，不改变默认筛选池。

## 数据与名称

| 市场 | 数据源 | 名称回退 |
| --- | --- | --- |
| A 股（沪深京） | AKShare 交易所列表、ETF 新浪列表 | 简体中文 → 英文 → 标准代码 |
| 港股 | 港交所中英文证券目录 | 简体中文 → 英文 → 标准代码 |
| 美股 | Nasdaq Trader `nasdaqtraded.txt` | 英文 → 标准代码 |

美股覆盖该目录内主要交易所的上市股票和 ETF，不包含 OTC。排除测试证券、
权证、权利、单位及 ETN。港股排除权证、债券等，ETF 包括杠杆和反向产品。
名称来自上游，不使用机器翻译；使用 OpenCC 将中文转简体。搜索支持中文、
繁体输入、英文名和代码。中文获取失败时保留已有中文缓存。

唯一股票资料物理表仍是 `stock_pools`。每只证券维护一条 `catalog` 记录，
保存中英文名称、标的类型、来源及最近出现时间；其他池只更新共享名称和类型。
池成员关系、排名、行业、价格和基本面不受同步影响。目录类型优先于历史 ETF
池的类型，避免将旧池中错误归类的股票继续显示为 ETF。

`db.py` 初始化资料字段，部署 SQL 为 `sql/021_stock_catalog_sync.sql`；worker
启动时幂等执行。`stock_catalog_sync_runs` 仅存执行审计，不是另一张股票资料表。

## 本地使用

以下命令均在 `stock_screener` 目录执行：

```bash
# 首次配置数据库连接后，手动跑一次（默认由子进程强制限制总时长）
.venv/bin/python -m worker.stock_catalog --once

# 今日执行结果
.venv/bin/python -m worker.stock_catalog --status

# 重试今日失败或部分完成来源；沿用当日预算，不重新抓成功来源
.venv/bin/python -m worker.stock_catalog --once --force

# 常驻运行，或使用 bash run_moneymanager.sh catalog-worker
.venv/bin/python -m worker.stock_catalog --daemon

# macOS 登录后常驻，并在异常退出后重启
.venv/bin/python -m worker.launch_agent install
.venv/bin/python -m worker.launch_agent status
.venv/bin/python -m worker.launch_agent stop
.venv/bin/python -m worker.launch_agent uninstall
```

LaunchAgent 名称：`com.moneymanager.stock-catalog`。安装器使用 `uv`，将当前
Python 依赖的精确版本从本机缓存离线安装到独立环境，复制必要运行代码到
`~/Library/Application Support/MoneyManager/stock-catalog/`。这避免后台任务
依赖桌面目录的访问权限，不修改 macOS 隐私设置。项目源码仍在 `worker/`。

只复制 MySQL 连接和 worker 设置到该目录的 `config.json`（权限 600，父目录
权限 700），保留密码原始字符；plist 不保存密码，不复制模型或飞书密钥。
代码或项目 `.env` 更新后重新运行 `install` 刷新后台运行副本及配置。
`stop` 停止本次登录会话中的任务；`uninstall` 同时移除 plist。需要恢复时重新
运行 `install`。电脑关机或休眠期间不会执行，恢复登录或唤醒后 60 秒内检查。

远端部署可使用同一 `python -m worker.stock_catalog --daemon` 命令，通过
环境变量注入数据库、请求预算和持久化缓存目录；本期不修改远端部署服务。
多个进程通过 MySQL 连接级互斥锁和每日执行记录去重。

## 调用量、性能和失败处理

美股一份目录、港股两个文件；A 股按交易所和 ETF 来源拆分。正常预估每日
13～43 次 HTTP 请求，实际以日志为准。不会因为同步 100 只或 30000 只股票
而产生对应数量的个股请求；K 线、行业、搜索和 LLM 调用均为 0。

- 同域名请求间隔至少 1 秒；连接/读取超时为 5/15 秒。
- 瞬时错误最多重试两次，退避 5、20 秒；429 使用 `Retry-After`。
- 默认每日最多 200 次实际请求尝试，总时长 900 秒。分别用
  `STOCK_CATALOG_REQUEST_LIMIT`、`STOCK_CATALOG_TIMEOUT_SEC`、
  `STOCK_CATALOG_REQUEST_INTERVAL_SEC` 配置；间隔不允许低于 1 秒。
- 请求计数写入数据库；重启、强制重试也不重置当日预算。
- 各来源独立提交。空列表、字段/代码异常、冲突重复和较上次成功快照减少
  超过 20% 的列表拒绝更新。没有历史快照时检查结构及代码，不猜测固定市场规模。
- 失败不删除旧证券；不因当天列表中未出现某只股票推断退市。
- 每批 500 条写入临时表，再通过集合 SQL 合并。持久化股票表只有 `stock_pools`。
- 当日成功来源的缓存用于中断恢复；保留最近 7 天，可用
  `STOCK_CATALOG_CACHE_DIR` 指定位置。
- `success` 表示所有来源完成；`partial` 表示部分成功或中文文件不可用；
  `failed` 表示全部失败。终态当日不自动重复，可手动 `--force` 重试失败或部分完成来源。

预计正常耗时 1～5 分钟，是容量估算而非上游保证。每次运行记录实际请求量、
增改数量、中英文/代码回退数量、来源日期、耗时和进程峰值内存。

本机 2026-11-05 的自动运行实测：22889 条目录记录（A 股及 ETF 7295、港股
及 ETF 3253、美股及 ETF 12341），28 次实际请求，耗时 104.3 秒，峰值内存
170.9 MiB。该轮全部来源成功，重复同步新增及变更数均为 0。30000 条模拟
目录测试的批量写入约 2.3 秒，搜索 p95 约 0.19 秒；结果随网络和机器变化。

后台日志：`~/Library/Application Support/MoneyManager/stock-catalog/logs/worker.log`
和 `worker-error.log`；后台缓存位于同一服务目录的 `cache/`。手动运行默认缓存
位于项目 `logs/stock_catalog/cache/`。审计可使用 `--status`
或查询 `stock_catalog_sync_runs`。发布前备份 `stock_pools`，首次运行后确认来源
数量和中文覆盖率，再启用常驻调度。

## 测试

```bash
PYTHONPATH=tests .venv/bin/python -m unittest test_catalog_worker
STOCK_CATALOG_TEST_MYSQL=1 PYTHONPATH=tests \
  .venv/bin/python -m unittest test_stock_catalog_mysql
```

MySQL 回归仅修改独立临时测试数据库，包含 30000 条目录的批量写入和搜索
性能测试。不会通过测试抓取真实上游数据或提交真实筛选任务。
