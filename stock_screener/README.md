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

使用 `daily_job.py` 将股票列表和K线历史写入 SQLite，默认每天执行一次即可。

```bash
python daily_job.py --markets HK,US --db data/market_data.db --log logs/daily_sync.jsonl
```

如果仅运行一次可不加 `--loop`；若希望容器内自动循环执行：

```bash
python daily_job.py --markets HK,US --db data/market_data.db --log logs/daily_sync.jsonl --loop
```

### Docker 部署

```bash
docker build -t stock-screener:latest .
docker run --rm -v $(pwd)/data:/app/data -v $(pwd)/logs:/app/logs stock-screener:latest
```

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
