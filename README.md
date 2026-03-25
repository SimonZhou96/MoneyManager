# MoneyManager

股票筛选（选股器）：**Web 前端 + FastAPI** 交互，以及 **`stock_screener/scheduled_daily_job.py`** 定时捞池、筛选、导出 CSV、飞书通知。

## 目录结构（精简后）

| 路径 | 说明 |
|------|------|
| `stock_screener/api/` | FastAPI 与 `screen_service` |
| `stock_screener/frontend/` | 选股器静态前端 |
| `stock_screener/jobs/` | 定时任务实现 `scheduled_daily_job.py` |
| `stock_screener/*.py` | 领域模块：`db`、`filters`、`strategizers`、`kline_fetcher` 等 |
| `scripts/` | CLI：`query_stock_pools`、`update_fundamentals`、smoke/熵检查 |
| `tests/` | pytest（含筛选管线集成测试） |

## 快速开始

```bash
cd stock_screener
pip install -r requirements.txt
# 配置 MySQL 后启动 API + 前端
./start_api.sh
```

定时任务（与前端独立）：

```bash
cd stock_screener
python3 scheduled_daily_job.py
```

详细说明见 [stock_screener/README.md](stock_screener/README.md)、根目录 [AGENTS.md](AGENTS.md)。
