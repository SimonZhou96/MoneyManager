# 港股/美股股票筛选器

基于EMA10向上突破EMA150策略的港股/美股筛选工具，并支持每日数据入库任务。

## 功能特性

1. **自动筛选**：筛选EMA10刚刚向上突破EMA150的港股/美股
2. **多数据源支持**：优先使用 AKShare，Futu OpenD 可选
3. **智能缓存**：K线数据自动缓存到本地文件，避免重复获取
4. **图表展示**：双击股票可查看详细K线图和技术指标
5. **技术指标**：显示EMA10、EMA150、HMA40、HMA200、HMA600
6. **详细日志**：显示每只股票的处理过程和筛选结果
7. **每日入库**：支持港股/美股股票列表与K线历史入库

## 安装依赖

```bash
pip install -r requirements.txt
```

**新增依赖**：
- `akshare`：用于港股/美股K线数据获取（主数据源）
- `pyarrow`：用于高效的parquet格式缓存（可选，如果不可用会自动使用CSV）

## 使用前准备

1. **安装FutuOpenD**（可选）
   - 下载并安装富途牛牛客户端
   - 在设置中开启OpenD服务
   - 确保OpenD正在运行（默认端口11111）
   - 登录账户

2. **安装AKShare**（主数据源）
   ```bash
   pip install akshare
   ```

## 运行程序

```bash
python main.py
```

### 命令行模式（无 GUI 环境）

```bash
python main.py --cli --output filtered_results.csv
```

可选输出结构化日志（JSON Lines）：

```bash
python main.py --cli --output filtered_results.csv --log output/screen_log_2026-01-27.jsonl
```

如果不提供 `--log`，默认输出到 `output/screen_log_{MARKET}_YYYY-MM-DD.jsonl`。

选择市场：

```bash
python main.py --cli --market HK --output filtered_results.csv
python main.py --cli --market US --output filtered_results.csv
```

## 每日数据入库任务

使用 `daily_job.py` 将股票列表和K线历史写入 MySQL，默认每天执行一次即可。

### 单股票同步模式

同步单个股票的近5年K线数据：

```bash
# 同步港股
python daily_job.py --stock-code HK.00700 --mysql-password your_password

# 同步美股
python daily_job.py --stock-code US.AAPL --mysql-password your_password
```

功能说明：
- 如果股票不在 `stocks` 表中，会自动获取并插入股票信息
- 自动获取近5年的K线数据并存储到 `kline_daily` 表
- 支持使用缓存数据（如果可用）
- 优先使用 AKShare，失败时使用 Futu OpenD（如果启用）

## K线图绘制工具

使用 `plot_kline.py` 从数据库查询并绘制股票K线图。

### 基本使用

```bash
# 绘制港股K线图
python plot_kline.py HK.00700 --mysql-password your_password

# 绘制美股K线图
python plot_kline.py US.AAPL --mysql-password your_password
```

### 高级选项

```bash
# 指定日期范围
python plot_kline.py HK.00700 \
  --start-date 2024-01-01 \
  --end-date 2024-12-31 \
  --mysql-password your_password

# 不显示成交量
python plot_kline.py HK.00700 --no-volume --mysql-password your_password

# 保存图表到文件
python plot_kline.py HK.00700 \
  --save output/kline_HK.00700.png \
  --mysql-password your_password
```

### 功能特性

- 📊 **专业K线图**：绘制标准的蜡烛图（红涨绿跌）
- 📈 **成交量显示**：下方显示成交量柱状图（可选）
- 📅 **日期范围**：支持指定开始和结束日期
- 💾 **保存功能**：支持保存为PNG图片文件
- 🎨 **美观界面**：自动显示价格统计信息（最高/最低/最新收盘价）

### 数据库要求

- **MySQL 版本**：支持 MySQL 5.7+ 和 MySQL 8.0+
- **字符集**：推荐使用 `utf8mb4`
- **认证方法**：
  - MySQL 5.7：默认使用 `mysql_native_password`（无需额外配置）
  - MySQL 8.0+：默认使用 `caching_sha2_password`（需要 `cryptography` 包，已包含在依赖中）

### 本地运行

```bash
python daily_job.py --markets HK,US --mysql-host 127.0.0.1 --mysql-user root --mysql-password your_password --log logs/daily_sync.jsonl
```

如果仅运行一次可不加 `--loop`；若希望自动循环执行：

```bash
python daily_job.py --markets HK,US --mysql-host 127.0.0.1 --mysql-user root --mysql-password your_password --log logs/daily_sync.jsonl --loop
```

### Docker/Podman 部署

#### 1. 构建镜像

**使用 Docker：**
```bash
cd stock_screener
docker build -t stock-screener:latest .
```

**使用 Podman：**
```bash
cd stock_screener
podman build -t stock-screener:latest .
```

#### 2. 运行容器

**基本运行（单次执行）：**
```bash
# Docker
docker run --rm \
  -v $(pwd)/logs:/app/logs \
  -e MYSQL_HOST=your_mysql_host \
  -e MYSQL_PORT=3306 \
  -e MYSQL_USER=root \
  -e MYSQL_PASSWORD=your_password \
  -e MYSQL_DATABASE=market_data \
  stock-screener:latest

# Podman
podman run --rm \
  -v $(pwd)/logs:/app/logs \
  -e MYSQL_HOST=your_mysql_host \
  -e MYSQL_PORT=3306 \
  -e MYSQL_USER=root \
  -e MYSQL_PASSWORD=your_password \
  -e MYSQL_DATABASE=market_data \
  stock-screener:latest
```

**循环执行（推荐用于生产环境）：**
```bash
# Podman（循环执行，每24小时执行一次）
podman run -d --name stock-screener \
  -v $(pwd)/logs:/app/logs \
  -e MYSQL_HOST=your_mysql_host \
  -e MYSQL_PORT=3306 \
  -e MYSQL_USER=root \
  -e MYSQL_PASSWORD=your_password \
  -e MYSQL_DATABASE=market_data \
  stock-screener:latest
```

**自定义参数运行：**
```bash
# Podman（覆盖默认参数）
podman run --rm \
  -v $(pwd)/logs:/app/logs \
  -e MYSQL_HOST=your_mysql_host \
  -e MYSQL_USER=root \
  -e MYSQL_PASSWORD=your_password \
  stock-screener:latest \
  python daily_job.py \
    --markets HK \
    --mysql-host your_mysql_host \
    --mysql-user root \
    --mysql-password your_password \
    --log logs/daily_sync.jsonl \
    --loop \
    --interval-hours 12
```

**如果 MySQL 在宿主机上，需要连接宿主机网络：**
```bash
# Podman（连接到宿主机网络）
podman run --rm \
  --network host \
  -v $(pwd)/logs:/app/logs \
  -e MYSQL_HOST=127.0.0.1 \
  -e MYSQL_USER=root \
  -e MYSQL_PASSWORD=your_password \
  stock-screener:latest
```

**查看日志：**
```bash
# 查看运行中的容器日志
podman logs -f stock-screener

# 查看挂载的日志文件
tail -f logs/daily_sync.jsonl
```

**停止和删除容器：**
```bash
# 停止容器
podman stop stock-screener

# 删除容器
podman rm stock-screener
```

**注意：** 
- 如果使用 IDE（如 Cursor/VS Code）的 Docker 扩展来构建，但实际使用的是 Podman，建议直接在终端使用 `podman build` 命令
- MySQL 密码建议使用环境变量文件（`.env`）或 Podman secrets 管理，避免在命令行中暴露
- 如果 MySQL 在容器中运行，可以使用 `--network` 参数连接同一网络，或使用容器名称作为主机名
- **MySQL 版本兼容性**：支持 MySQL 5.7 和 MySQL 8.0+，`cryptography` 包已包含以支持所有认证方法

## 使用说明

1. 启动程序后，选择市场（HK/US），点击"开始筛选"按钮
2. 程序会自动：
   - 获取所选市场的股票列表（带缓存）
   - 对每只股票获取历史K线数据：
     - 优先从本地缓存读取（如果存在且是最新的）
  - 如果缓存不存在或过期，优先使用 AKShare 获取
  - 如果 AKShare 失败，再尝试使用 Futu OpenD 获取（若已连接）
     - 获取成功后自动保存到缓存
   - 计算技术指标（EMA10、EMA150、HMA等）
   - 筛选符合条件的股票
3. 结果直接显示在表格中
4. 双击任意股票可查看详细图表

## 筛选条件

- EMA10在前一个交易日 < EMA150
- EMA10在当前交易日 > EMA150（严格大于，表示向上突破）
- 需要至少150个交易日的历史数据

## 技术指标说明

- **EMA10/EMA150**：指数移动平均线
- **HMA40/200/600**：Hull移动平均线
- 所有指标基于前复权价格计算

## 缓存机制

### K线数据缓存
- **位置**：`cache/kline_data/` 目录
- **格式**：Parquet格式（优先）或CSV格式（fallback）
- **刷新策略**：
  - 如果缓存数据的最新日期是今天或昨天，直接使用缓存
  - 否则重新获取数据并更新缓存
- **文件命名**：`{市场}_{股票代码}.parquet` 或 `{市场}_{股票代码}.csv`
  - 例如：`HK_00700.parquet`、`US_AAPL.parquet`

### 股票列表缓存
- **位置**：`cache/hk_stocks.json` 或 `cache/us_stocks.json`
- **刷新策略**：按天刷新

### 筛选结果缓存
- **位置**：`cache/hk_filtered_results.json` 或 `cache/us_filtered_results.json`
- **刷新策略**：按天刷新

## 数据源优先级

1. **本地缓存**（最快）
2. **AKShare**（优先）
3. **富途API**（可选 fallback）

## 注意事项

- 首次运行可能需要较长时间（需要获取大量股票数据）
- 建议在网络稳定的环境下运行
- 富途API有额度限制（60次/30秒），程序会自动限流
- 即使未连接 OpenD，程序也可以使用 AKShare 正常运行
- K线数据会自动缓存，避免重复获取浪费额度
- 缓存文件会占用磁盘空间，建议定期清理

## 文件结构

```
stock_screener/
├── main.py              # 主程序
├── kline_fetcher.py     # K线数据获取器（支持多数据源和缓存）
├── daily_job.py         # 每日数据入库任务
├── db.py                # SQLite 数据库访问
├── market.py            # 市场配置与工具
├── universe.py          # 股票列表获取
├── requirements.txt      # 依赖包
├── README.md           # 使用说明
├── cache/              # 缓存目录
│   ├── hk_stocks.json           # 股票列表缓存
│   ├── filtered_results.json    # 筛选结果缓存
│   └── kline_data/              # K线数据缓存
│       ├── HK_00700.parquet
│       ├── HK_00001.parquet
│       └── ...
├── data/               # SQLite 数据库目录
│   └── market_data.db
├── logs/               # 每日入库日志
│   └── daily_sync.jsonl
└── .gitignore          # Git忽略文件
```
