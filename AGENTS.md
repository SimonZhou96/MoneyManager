# MoneyManager / stock_screener — Agent 说明

面向在本仓库使用 Cursor / 其他 AI Agent 的约定，与 [Harness Engineering](https://martinfowler.com/articles/exploring-gen-ai/harness-engineering.html) 思路一致：**人定边界与反馈，Agent 在约束内改代码与跑检查**。

## 架构与主流程（改代码时优先对齐）

| 主流程 | 位置 |
|--------|------|
| **Web 选股器** | `stock_screener/api/` + `stock_screener/frontend/`（`start_api.sh`） |
| **定时选股** | `stock_screener/jobs/scheduled_daily_job.py`，入口 `stock_screener/scheduled_daily_job.py` |
| **领域逻辑** | `db.py`、`filters.py`、`strategizers.py`、`strategy.py`、`kline_fetcher.py`、`api/screen_service.py` 等 |
| **运维 CLI** | 仓库根目录 `scripts/`（查股票池、更新基本面、飞书测试、smoke/熵检查） |

已移除与主流程无关的遗留：`main.py`（旧 GUI）、`daily_job.py` / `screen_with_filters.py`（旧命令行）、`plot_kline.py`、`sector_fetcher.py`（无引用）、散落调试 `test_*.py`。

## 环境与密钥（勿提交真实生产密码）

| 变量 | 默认 | 说明 |
|------|------|------|
| `MYSQL_HOST` | `127.0.0.1` | MySQL 地址 |
| `MYSQL_PORT` | `3306` | 端口 |
| `MYSQL_USER` | `root` | 用户 |
| `MYSQL_PASSWORD` | `123456` | 与本地/CI 一致即可；生产用密钥管理，不要写进仓库 |
| `MYSQL_DATABASE` | `market_data` | 业务库名 |

本地需已建库：`CREATE DATABASE market_data CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;`

## 禁止 / 谨慎大改清单

- **不要随意**：全目录重命名、大规模格式化整个 `stock_screener`（除非用户明确要求）、删除他人注释与无关逻辑、引入与任务无关的新框架。
- **不要**：把真实数据库密码、Token、私钥写入代码或提交到 Git。
- **优先**：小步修改、对齐现有模块风格（`db.py`、`api/`、`filters.py` 等）；拿不准时先问用户（与 `CLAUDE.md` 一致）。
- **依赖**：以 `stock_screener/requirements.txt` 为准；根目录 `requirements.txt` 仅 `-r` 转发；`requirements-dev.txt` 为 pytest、ruff。

## 熵管理（技术债与信噪比）

对应 Harness 里的 **熵管理**：Agent 容易复制坏模式、堆冗余，要靠**小步、持续、可机器守门**来压熵增。

- **原则**：能写进仓库的约定不写「口头」；改到哪个模块，顺带清掉该文件内**明显死代码/过时注释**（不大范围无关重构）；文档与对外行为不一致时优先改文档或补测试。
- **机器守门**：CI 对 `stock_screener` 跑 **Ruff 严重项**（未定义名、语法类），见 `.github/workflows/ci.yml`。集成测试守住筛选主链路。
- **人工节奏**：发版或合并较大功能前，本地跑 `./scripts/check_entropy.sh`；**进阶**可逐步还「未使用 import」债：`python -m ruff check stock_screener --select F401,F841`（当前仓库可能有多条，宜分 PR 修，勿一次大扫）。

## 常用命令

```bash
# 安装开发与测试依赖
pip install -r stock_screener/requirements.txt -r requirements-dev.txt

# 格式检查（CI 对 tests/、scripts/ 强制执行）
python -m ruff format --check tests scripts

# 筛选管线集成测试（需 MySQL + 外网拉 K 线）
PYTHONPATH=stock_screener pytest tests/test_screen_pipeline_integration.py -v -m integration

# 或（需 chmod +x scripts/run_smoke_screen.sh）
./scripts/run_smoke_screen.sh

# 熵管理：严重级静态检查（stock_screener）
./scripts/check_entropy.sh
```

## 测试说明

- **集成测试** `tests/test_screen_pipeline_integration.py`：港股 / 美股 / A 股各一只，走 `run_screening_task`（含 fetcher、筛选器链、策略器链）。无 MySQL 或网络失败时会跳过或失败，见测试内说明。
