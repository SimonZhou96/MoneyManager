# 选股器 API 使用指南

## 快速开始

### 1. 安装依赖

```bash
cd stock_screener
pip install -r requirements.txt
```

### 2. 配置数据库

确保 MySQL 服务已启动，并创建数据库：

```sql
CREATE DATABASE market_data CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 3. 配置环境变量（可选）

```bash
export MYSQL_HOST=127.0.0.1
export MYSQL_PORT=3306
export MYSQL_USER=root
export MYSQL_PASSWORD=your_password
export MYSQL_DATABASE=market_data
```

### 4. 启动 API 服务

```bash
# 使用启动脚本
./start_api.sh

# 或直接使用 uvicorn
python3 -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

### 5. 访问应用

- **前端页面**: http://localhost:8000
- **API 文档**: http://localhost:8000/docs
- **健康检查**: http://localhost:8000/api/health

---

## API 接口说明

### 1. 获取市场列表

**请求**:
```
GET /api/markets
```

**响应**:
```json
{
  "markets": [
    {"value": "HK", "label": "港股"},
    {"value": "US", "label": "美股"},
    {"value": "A", "label": "A股"}
  ]
}
```

---

### 2. 获取 Timeframe 列表

**请求**:
```
GET /api/timeframes
```

**响应**:
```json
{
  "timeframes": [
    {"value": "1m", "label": "1分钟"},
    {"value": "5m", "label": "5分钟"},
    {"value": "1h", "label": "1小时"},
    {"value": "1d", "label": "1日"},
    ...
  ]
}
```

---

### 3. 启动筛选任务

**请求**:
```
POST /api/screen
Content-Type: application/json

{
  "market": "HK",
  "timeframe": "1d",
  "market_cap_min": 5000000000,
  "avg_daily_volume_min": 20000000,
  "price_min": 5,
  "price_max": 1000,
  "pe_min": 10,
  "pe_max": 100,
  "require_profitable": true
}
```

**参数说明**:
- `market`: 市场（必填）- "HK", "US", "A"
- `timeframe`: 时间周期（必填）- 1m, 5m, 1h, 1d 等
- `market_cap_min`: 最小市值（可选）
- `market_cap_max`: 最大市值（可选）
- `avg_daily_volume_min`: 最小每日平均交易量（可选）
- `avg_daily_volume_max`: 最大每日平均交易量（可选）
- `price_min`: 最小股票价格（可选）
- `price_max`: 最大股票价格（可选）
- `pe_min`: 最小市盈率（可选）
- `pe_max`: 最大市盈率（可选）
- `require_profitable`: 是否要求公司盈利（可选，默认 true）

**响应**:
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

---

### 4. 查询筛选进度

**请求**:
```
GET /api/progress/{task_id}
```

**响应**:
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "running",
  "total_count": 2000,
  "completed_count": 500,
  "current_stock_code": "HK.00700",
  "current_stock_name": "腾讯控股",
  "market": "HK",
  "timeframe": "1d"
}
```

**status 取值**:
- `running`: 运行中
- `completed`: 已完成
- `failed`: 失败

---

### 5. 获取筛选结果

**请求**:
```
GET /api/results/{task_id}?passed_only=true
```

**参数**:
- `passed_only`: 是否只返回通过筛选的股票（默认 true）

**响应**:
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "results": [
    {
      "code": "HK.00700",
      "name": "腾讯控股",
      "is_passed": true,
      "filter_summary": "passed=5, failed=0, skipped=0",
      "sector": "科技",
      "industry": "互联网",
      "market_cap": 3000000000000,
      "pe_ratio": 25.5,
      "close_price": 350.5
    }
  ],
  "total": 1
}
```

---

## 前端使用

### 页面功能

1. **筛选条件区域**
   - 选择市场（A股、港股、美股）
   - 选择 timeframe（1分钟~3月）
   - 输入筛选指标（支持中文单位：亿、万）
   - 点击"开始筛选"按钮

2. **进度展示区域**
   - 当前处理股票面板：显示正在处理的股票代码和名称
   - 进度条：显示已完成 / 总数
   - 状态文字：显示当前状态（运行中、完成、失败）
   - 每 5 秒自动更新一次

3. **结果展示区域**
   - 显示通过筛选的股票列表
   - 包含：代码、名称、板块、市值、价格、市盈率

### 支持的输入格式

**市值**:
- `50亿` 或 `50億` → 5,000,000,000
- `5e9` → 5,000,000,000
- `5000000000` → 5,000,000,000

**每日平均交易量**:
- `2000万` 或 `2000萬` → 20,000,000
- `2e7` → 20,000,000
- `20000000` → 20,000,000

---

## 技术架构

### 后端

- **FastAPI**: REST API 框架
- **uvicorn**: ASGI 服务器
- **PyMySQL**: MySQL 数据库驱动
- **pandas**: 数据处理
- **yfinance / AKShare**: K 线数据获取

### 前端

- **HTML5 + CSS3**: 页面结构和样式
- **原生 JavaScript**: 交互逻辑
- **Fetch API**: HTTP 请求

### 数据库

- **MySQL 5.7+**: 数据存储
- **表结构**:
  - `stocks`: 股票主数据
  - `screening_tasks`: 筛选任务
  - `screening_results`: 筛选结果
  - `ema_breakout_signals_{timeframe}`: EMA 信号（按 timeframe 分表）

---

## 设计原则

✅ **高内聚、低耦合**

- 每个模块职责单一
- 筛选器之间相互独立
- API 层与业务逻辑分离
- 前后端完全解耦

✅ **可扩展性**

- 筛选器链模式，易于添加新筛选器
- 工厂模式创建数据获取器
- 插件化架构

✅ **用户体验**

- 实时进度展示
- 5 秒轮询，及时反馈
- 现代化 UI 设计
- 响应式布局

---

## 常见问题

### Q1: 启动失败，提示端口被占用

**A**: 修改端口号：
```bash
python3 -m uvicorn api.main:app --host 0.0.0.0 --port 8080
```

### Q2: 数据库连接失败

**A**: 检查 MySQL 配置和环境变量：
```bash
export MYSQL_PASSWORD=your_actual_password
```

### Q3: 筛选任务一直显示"运行中"

**A**: 可能是后台任务异常，检查：
1. 数据库是否正常
2. 股票列表是否为空
3. 查看 FastAPI 控制台日志

### Q4: 前端页面无法访问

**A**: 确认：
1. API 服务已启动
2. frontend 目录存在
3. 访问 http://localhost:8000（不是 8000/frontend）

---

## 生产部署建议

### 1. 使用生产级 ASGI 服务器

```bash
pip install gunicorn
gunicorn api.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

### 2. 配置 Nginx 反向代理

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 3. 限制 CORS 来源

修改 `api/main.py`:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://your-domain.com"],  # 限制为实际域名
    ...
)
```

### 4. 使用 systemd 管理服务

创建 `/etc/systemd/system/stock-screener.service`:
```ini
[Unit]
Description=Stock Screener API
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/stock_screener
Environment="MYSQL_PASSWORD=your_password"
ExecStart=/path/to/venv/bin/gunicorn api.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
Restart=always

[Install]
WantedBy=multi-user.target
```

启动服务:
```bash
sudo systemctl start stock-screener
sudo systemctl enable stock-screener
```

---

## 开发指南

### 添加新的筛选器

1. 在 `filters.py` 中继承 `Filter` 基类
2. 实现 `apply()` 方法
3. 在 `api/screen_service.py` 的 `create_filter_chain_from_params()` 中添加逻辑

### 添加新的数据源

1. 在 `kline_fetcher.py` 中继承 `KlineFetcherBase`
2. 实现 `fetch()` 方法
3. 在 `KlineFetcherFactory` 中注册

---

## 联系方式

如有问题或建议，请提交 Issue 或 Pull Request。
