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

### 第五步：首次运行

```bash
# 日线扫描港股（限制 10 只测试）
python daily_job.py --markets HK --timeframe 1d --limit 10 --mysql-password your_password

# 1 小时线扫描美股
python daily_job.py --markets US --timeframe 1h --limit 10 --mysql-password your_password

# 5 分钟线扫描 A 股
python daily_job.py --markets A --timeframe 5m --limit 10 --mysql-password your_password
```

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

### 命令行参数（daily_job.py）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--markets` | US | 市场列表：HK,US,A |
| `--timeframe` | 1d | K 线周期：1m,5m,1h,1d,1wk,1mo 等 |
| `--limit` | 无 | 限制股票数量（测试用） |
| `--loop` | 否 | 是否循环执行 |
| `--interval-hours` | 24 | 循环间隔（小时） |
| `--use-futu` | 否 | 启用 Futu OpenD 作为备用数据源 |

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

### 1. 扫描任务（daily_job.py）

```bash
# 单次执行
python daily_job.py --markets HK,US,A --timeframe 1d --mysql-password your_password

# 循环执行
python daily_job.py --markets HK,US --timeframe 1h --loop --interval-hours 1
```

### 2. 筛选器（screen_with_filters.py）

```bash
# EMA 突破 + 日线
python screen_with_filters.py --market HK --timeframe 1d --use-ema

# EMA + PE 过滤 + 1h 线
python screen_with_filters.py --market A --timeframe 1h --use-ema --min-pe 0 --max-pe 50
```

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

## 文件结构

```
stock_screener/
├── daily_job.py         # 主扫描任务（简化版：API K线 -> 内存计算 -> 写结果）
├── screen_with_filters.py  # 多维度筛选器链
├── timeframe.py         # Timeframe 配置与校验
├── kline_fetcher.py     # K 线获取器（工厂模式：YFinance/AKShare/Futu）
├── strategy.py          # EMA 突破策略
├── db.py                # MySQL 存储（stocks + ema信号分表 + 筛选结果）
├── market.py            # 市场配置（HK/US/A）
├── universe.py          # 股票列表获取
├── filters.py           # 筛选器架构
├── universe_filter.py   # 股票池缩减器
├── sector_fetcher.py    # 板块信息获取
├── main.py              # GUI 模式（TODO: 待适配）
├── plot_kline.py        # K 线绘图（TODO: 待适配）
├── requirements.txt     # 依赖
├── Dockerfile           # Docker 镜像
├── deploy.sh            # Podman 部署脚本
└── logs/                # 日志
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
