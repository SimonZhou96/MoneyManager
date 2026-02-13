# MoneyManager 股票筛选器

基于 EMA10 向上突破 EMA150 策略的多市场股票筛选系统，支持港股、美股、A 股。支持每日数据入库、多维度筛选、K 线绘图。

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
| **EMA 突破筛选** | EMA10 向上突破 EMA150（T-1 或 T-2 突破） |
| **多维度筛选** | 市值、PE、板块、换手率、成交量等 |
| **多数据源** | AKShare（主）→ YFinance → Futu OpenD（可选） |
| **MySQL 持久化** | 股票列表、K 线、EMA 信号、筛选结果入库 |
| **增量同步** | 仅拉取缺失日期，支持本地缓存 |
| **K 线绘图** | 从数据库查询并绘制 K 线图 |

---

## 环境要求

| 项目 | 要求 |
|------|------|
| Python | 3.10+ |
| MySQL | 5.7+ 或 8.0+ |
| 网络 | 可访问东方财富等数据源（AKShare） |
| 可选 | Futu OpenD（富途牛牛 + OpenD 服务） |

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
source .venv/bin/activate   # Linux/macOS
# 或 Windows: .venv\Scripts\activate
```

### 第三步：安装依赖

```bash
pip install -r requirements.txt
```

主要依赖：`akshare`、`pandas`、`PyMySQL`、`pyarrow`、`matplotlib` 等。

### 第四步：准备 MySQL 数据库

1. **安装 MySQL**（若未安装）：
   - macOS: `brew install mysql` 或从官网下载
   - Linux: `apt install mysql-server` / `yum install mysql-server`
   - Windows: 从 [MySQL 官网](https://dev.mysql.com/downloads/mysql/) 下载安装

2. **创建数据库和用户**：

```sql
-- 登录 MySQL
mysql -u root -p

-- 创建数据库
CREATE DATABASE market_data CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 创建用户（可选，推荐不用 root）
CREATE USER 'market_user'@'%' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON market_data.* TO 'market_user'@'%';
FLUSH PRIVILEGES;
```

3. **表结构**：首次运行 `daily_job.py` 时会自动创建 `stocks`、`kline_daily`、`ema_breakout_signals`、`screening_results` 等表，无需手动建表。

### 第五步：首次运行（单次同步）

```bash
# 同步港股（限制 10 只，用于验证）
python daily_job.py --markets HK --limit 10 --mysql-password your_password

# 同步美股
python daily_job.py --markets US --limit 10 --mysql-password your_password

# 同步 A 股
python daily_job.py --markets A --limit 10 --mysql-password your_password
```

若输出正常，说明部署成功。

### 第六步：执行筛选

```bash
# 筛选港股（EMA 突破）
python screen_with_filters.py --market HK --use-ema --mysql-password your_password

# 筛选 A 股，并限制 PE 0-50
python screen_with_filters.py --market A --use-ema --min-pe 0 --max-pe 50 --mysql-password your_password
```

### 第七步：绘制 K 线图（可选）

```bash
python plot_kline.py HK.00700 --mysql-password your_password
python plot_kline.py 000001.SZ --mysql-password your_password
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

可通过环境变量或命令行参数传入，命令行优先。

### 命令行参数（daily_job.py）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--markets` | US | 市场列表：HK,US,A |
| `--limit` | 无 | 限制股票数量（测试用） |
| `--loop` | 否 | 是否循环执行 |
| `--interval-hours` | 24 | 循环间隔（小时） |
| `--use-futu` | 否 | 启用 Futu OpenD 作为备用数据源 |
| `--stock-code` | 无 | 仅同步单只股票，如 HK.00700、000001.SZ |

### 股票代码格式

| 市场 | 格式 | 示例 |
|------|------|------|
| 港股 | HK.XXXXX | HK.00700 |
| 美股 | US.XXXX 或 XXXX | US.AAPL、AAPL |
| A 股（上交所） | XXXXXX.SS | 600000.SS |
| A 股（深交所） | XXXXXX.SZ | 000001.SZ |

---

## 使用方式

### 1. 每日数据同步（推荐定时任务）

```bash
# 单次执行
python daily_job.py --markets HK,US,A --mysql-password your_password --log logs/daily_sync.jsonl

# 循环执行（每 24 小时）
python daily_job.py --markets HK,US,A --mysql-password your_password --log logs/daily_sync.jsonl --loop
```

### 2. 单只股票同步

```bash
python daily_job.py --stock-code HK.00700 --mysql-password your_password
python daily_job.py --stock-code 000001.SZ --mysql-password your_password
```

### 3. 筛选器（screen_with_filters.py）

```bash
# 仅 EMA 突破
python screen_with_filters.py --market HK --use-ema

# EMA + 市值 + PE
python screen_with_filters.py --market A --use-ema --min-market-cap 1e9 --min-pe 0 --max-pe 50

# 股票池预筛 + 筛选
python screen_with_filters.py --market HK --use-ema --universe-min-cap 5e8 --universe-max-pe 30
```

### 4. GUI 模式（main.py）

```bash
python main.py
```

选择市场后点击「开始筛选」，支持双击查看 K 线图。

### 5. 命令行模式（无 GUI）

```bash
python main.py --cli --market HK --output filtered_results.csv
```

---

## Docker / Podman 部署

### 构建镜像

```bash
cd stock_screener
docker build -t stock-screener:latest .
# 或: podman build -t stock-screener:latest .
```

### 运行容器

```bash
# 单次执行
docker run --rm \
  -v $(pwd)/logs:/app/logs \
  -e MYSQL_HOST=host.docker.internal \
  -e MYSQL_PASSWORD=your_password \
  stock-screener:latest

# 循环执行（后台）
docker run -d --name stock-screener \
  -v $(pwd)/logs:/app/logs \
  -e MYSQL_HOST=host.docker.internal \
  -e MYSQL_PASSWORD=your_password \
  -e MARKETS=HK,US,A \
  stock-screener:latest
```

### 使用 deploy.sh（Podman）

```bash
# 构建
MYSQL_PASSWORD=your_password ./deploy.sh build

# 运行
MYSQL_PASSWORD=your_password ./deploy.sh run

# 查看日志
./deploy.sh logs

# 停止
./deploy.sh stop
```

---

## 文件结构

```
stock_screener/
├── main.py              # 主程序（GUI + 命令行）
├── daily_job.py         # 每日数据同步任务
├── screen_with_filters.py  # 筛选器链（多维度筛选）
├── plot_kline.py       # K 线绘图
├── db.py               # MySQL 数据库访问
├── market.py            # 市场配置（HK/US/A）
├── universe.py         # 股票列表获取（AKShare/Futu）
├── kline_fetcher.py    # K 线获取（AKShare/YFinance/Futu）
├── sector_fetcher.py   # 板块信息获取
├── strategy.py         # EMA 突破策略
├── filters.py          # 筛选器架构
├── universe_filter.py  # 股票池缩减器
├── requirements.txt    # 依赖
├── Dockerfile         # Docker 镜像
├── deploy.sh          # Podman 部署脚本
├── cache/             # 本地缓存
│   ├── kline_data/    # K 线 parquet/csv
│   └── *_sectors.json # 板块缓存
├── logs/              # 同步日志
│   └── daily_sync.jsonl
└── output/            # 输出目录
```

---

## 数据源说明

| 数据 | 主数据源 | 备用 |
|------|----------|------|
| 股票列表 | AKShare | Futu OpenD |
| K 线 | AKShare | YFinance → Futu |
| 板块信息 | AKShare | Futu |

- **AKShare**：免费，无需登录，需网络访问东方财富等
- **Futu OpenD**：需安装富途牛牛并开启 OpenD，有额度限制

---

## 常见问题

### 1. 连接 MySQL 失败

- 确认 MySQL 已启动：`mysql -u root -p -e "SELECT 1"`
- 检查防火墙、端口、用户名密码
- MySQL 8.0 需支持 `caching_sha2_password`（已包含 `cryptography` 依赖）

### 2. AKShare 获取失败

- 检查网络（需访问 cn 数据源）
- 可尝试 `--use-futu` 使用 Futu 作为备用

### 3. 首次同步较慢

- 全市场股票较多，建议先用 `--limit 50` 验证
- 数据会写入 MySQL 和本地缓存，后续为增量同步

### 4. 无 GUI 环境

- 使用 `main.py --cli` 或 `screen_with_filters.py` 命令行模式

### 5. Docker 中连接宿主机 MySQL

- Linux: `MYSQL_HOST=172.17.0.1` 或 `--network host`
- macOS/Windows: `MYSQL_HOST=host.docker.internal`

---

## 许可证

请参考项目根目录的 LICENSE 文件。
