# MoneyManager 股票筛选器

基于 EMA10 向上突破 EMA150 策略的多市场股票筛选系统，支持港股、美股、A 股，支持多 timeframe（1m~3mo）。

---

## 目录

- [功能特性](#功能特性)
- [环境要求](#环境要求)
- [从零开始部署](#从零开始部署)
- [配置说明](#配置说明)
- [使用方式](#使用方式)
- [文件结构](#文件结构)
- [常见问题](#常见问题)

---

## 功能特性

| 功能 | 说明 |
|------|------|
| **多市场** | 港股 (HK)、美股 (US)、A 股 (A) |
| **多 Timeframe** | 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo |
| **EMA 突破筛选** | EMA10 向上突破 EMA150（T-1 或 T-2 突破） |
| **多维度筛选** | 市值、PE、板块、换手率等（filter 链） |
| **多数据源** | YFinance（主）→ AKShare → Futu OpenD（可选） |
| **MySQL 结果存储** | 按 timeframe 分表：`ema_breakout_signals_{timeframe}` |
| **轻量化** | K 线不入库，内存获取 + 计算 + 写入结果 |

---

## 环境要求

| 项目 | 要求 |
|------|------|
| Python | 3.10+ |
| MySQL | 5.7+ 或 8.0+ |
| 网络 | 可访问 Yahoo Finance / 东方财富 |
| 可选 | Futu OpenD |

---

## 从零开始部署

### 第一步：克隆代码

```bash
git clone https://github.com/your-org/MoneyManager.git
cd MoneyManager/stock_screener
```

### 第二步：创建 Python 虚拟环境（推荐）

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 第三步：安装依赖

```bash
pip install -r requirements.txt
```

### 第四步：准备 MySQL 数据库

```sql
CREATE DATABASE market_data CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

表结构由程序自动创建（`stocks`、`ema_breakout_signals_{timeframe}`、`screening_results`）。

### 第五步：启动 Web 选股器（主交互）

```bash
./start_api.sh
# 浏览器打开 http://localhost:8000
```

### 定时任务（捞股票池 + 全池筛选 + CSV + 飞书）

与前端独立，在 `stock_screener` 目录执行：

```bash
python3 scheduled_daily_job.py
python3 scheduled_daily_job.py --no-fetch   # 仅筛选（不捞池）
python3 scheduled_daily_job.py --no-feishu  # 不发飞书
```

实现代码位于 `jobs/scheduled_daily_job.py`，根目录 `scheduled_daily_job.py` 为薄入口。

---

## 配置说明

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MYSQL_HOST` | 127.0.0.1 | MySQL 主机 |
| `MYSQL_PORT` | 3306 | MySQL 端口 |
| `MYSQL_USER` | root | MySQL 用户名 |
| `MYSQL_PASSWORD` | 123456 | MySQL 密码 |
| `MYSQL_DATABASE` | market_data | 数据库名 |

### 辅助 CLI（仓库根目录 `scripts/`）

| 脚本 | 说明 |
|------|------|
| `scripts/query_stock_pools.py` | 查询已入库的股票池 |
| `scripts/update_fundamentals.py` | 用 Futu 更新 `stocks` 基本面 |
| `scripts/test_feishu_webhook.py` | 测试飞书 Webhook |

### 支持的 Timeframe

| 级别 | 可选值 |
|------|--------|
| 分钟级 | 1m, 2m, 5m, 15m, 30m, 60m, 90m |
| 小时级 | 1h |
| 日级及以上 | 1d, 5d, 1wk, 1mo, 3mo |

**注意**：分钟级数据受数据源限制（YFinance 1m 仅 7 天，5m/1h 仅 60 天）。

### 股票代码格式

| 市场 | 格式 | 示例 |
|------|------|------|
| 港股 | HK.XXXXX | HK.00700 |
| 美股 | US.XXXX | US.AAPL |
| A 股（上交所） | XXXXXX.SS | 600000.SS |
| A 股（深交所） | XXXXXX.SZ | 000001.SZ |

---

## 使用方式

- **日常选股**：`./start_api.sh`，使用前端 + `/api/*`。
- **定时批处理**：`python3 scheduled_daily_job.py`（见上文）。
- **运维脚本**：见 `../scripts/`（需在仓库根目录执行，已自动加入 `stock_screener` 到 `PYTHONPATH`）。

---

## Docker / Podman 部署

```bash
# 构建
docker build -t stock-screener:latest .

# 运行
docker run -d --name stock-screener \
  -e MYSQL_HOST=host.docker.internal \
  -e MYSQL_PASSWORD=your_password \
  stock-screener:latest

# 使用 deploy.sh
TIMEFRAME=1h MYSQL_PASSWORD=your_password ./deploy.sh run
```

---

## 文件结构（核心）

```
stock_screener/
├── api/                 # FastAPI（screen、chart、stockpool、watchlist…）
├── frontend/            # 选股器静态页面
├── jobs/                # scheduled_daily_job 实现（定时捞池+筛选+导出+飞书）
├── scheduled_daily_job.py   # 定时任务薄入口（转发 jobs）
├── db.py / filters.py / strategizers.py / strategy.py
├── kline_fetcher.py / timeframe.py / market.py / universe*.py
├── fetch_stock_pools.py / stock_pool.py / feishu_*.py
├── requirements.txt / Dockerfile / deploy.sh / start_api.sh
└── logs/

# 仓库根目录
../scripts/              # 运维 CLI（查股票池、更新基本面、飞书测试、smoke）
../tests/                # pytest 集成测试
```

---

## 数据流

```
1. fetch_stock_list (AKShare/Futu) -> stocks 表
2. 对每只股票:
   KlineFetcher.fetch(code, market, timeframe) -> 内存 DataFrame
3. analyze_stock_ema_breakout(df) -> EMABreakoutSignal
4. db.upsert_ema_breakout_signal(timeframe, ...) -> ema_breakout_signals_{timeframe} 表
```

K 线数据**不入库**，仅在内存中使用。

---

## 常见问题

### 1. 分钟级数据量不够 152 根

分钟级数据受限于数据源的历史回溯深度。例如 YFinance 的 1m 仅支持最近 7 天。如果 7 天的 1m K 线不足 152 根（EMA150 需要），将返回 INSUFFICIENT_DATA。

**建议**：分钟级 timeframe 适用于高频交易市场（如 US），日内 K 线根数充足。

### 2. AKShare 获取失败

程序会自动 fallback 到 YFinance。确保网络可访问 Yahoo Finance。

### 3. Docker 中连接宿主机 MySQL

- Linux: `MYSQL_HOST=172.17.0.1` 或 `--network host`
- macOS/Windows: `MYSQL_HOST=host.docker.internal`
