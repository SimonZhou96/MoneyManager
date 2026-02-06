# 股票筛选系统架构分析

## 📋 项目概述

这是一个基于EMA（指数移动平均线）技术指标的股票筛选系统，支持港股和美股市场。系统可以自动筛选出EMA10向上突破EMA150的股票，并提供GUI界面和命令行工具进行数据查询、分析和可视化。

---

## 🏗️ 模块组成

### 1. **主程序模块 (main.py)**
**职责：** 系统入口，提供GUI和CLI两种交互方式

**核心类：**
- `RateLimiter`: API请求限流器，控制API调用频率（60次/30秒）
- `StockScreener`: 股票筛选器主类，整合所有功能

**主要功能：**
- 连接Futu OpenD或使用AKShare获取数据
- 获取股票列表（支持缓存）
- 筛选满足EMA突破条件的股票
- 计算技术指标（EMA10, EMA150, HMA40/200/600）
- 提供GUI界面（Tkinter）和CLI命令行模式
- 绘制股票K线图表

---

### 2. **股票列表获取模块 (universe.py)**
**职责：** 从多个数据源获取市场股票列表

**核心函数：**
- `fetch_stock_list_akshare()`: 从AKShare获取股票列表
- `fetch_stock_list_futu()`: 从富途API获取股票列表
- `_normalize_hk_code()`: 规范化港股代码格式
- `_normalize_us_code()`: 规范化美股代码格式

**数据源优先级：**
1. AKShare（免费，无需认证）
2. Futu OpenAPI（需要本地运行OpenD）

---

### 3. **策略模块 (strategy.py)**
**职责：** 实现EMA突破策略的核心逻辑

**核心类：**
- `EMABreakoutResult`: 突破结果枚举类
  - `BREAKOUT_T1`: 前一天突破
  - `BREAKOUT_T2`: 前两天突破
  - `NO_BREAKOUT_BELOW`: EMA10在下方
  - `NO_BREAKOUT_ALREADY_ABOVE`: 早已在上方
  - `NO_BREAKOUT_DOWNWARD`: 向下跌破
  - `INSUFFICIENT_DATA`: 数据不足
  - `INVALID_DATA`: 数据异常

- `EMABreakoutSignal`: 突破信号数据类
  - 包含市场、代码、检查日期、结果、EMA值等信息

**核心函数：**
- `calculate_ema()`: 计算指数移动平均线
- `check_ema_breakout()`: 检查EMA突破条件
- `analyze_stock_ema_breakout()`: 分析单只股票的EMA突破情况

**策略规则：**
- 前一日：EMA10 ≤ EMA150
- 当前日：EMA10 ≥ EMA150
- 需要至少150个交易日的历史数据
- 支持检查前1-2个交易日内的突破

---

### 4. **数据库模块 (db.py)**
**职责：** MySQL数据持久化层

**核心类：**
- `MySqlConfig`: 数据库配置类（使用dataclass）
- `MarketDatabase`: 数据库操作类

**数据表结构：**

#### 4.1 stocks表（股票主数据）
- 唯一约束：`(market, code)`
- 字段：market, code, name, exchange, currency, lot_size, status, listing_date, delisting_date, source
- 自动维护：created_at, updated_at

#### 4.2 kline_daily表（日K线数据）
- 唯一约束：`(market, code, trade_date, adj_type)`
- 字段：market, code, trade_date, open, high, low, close, last_close, volume, turnover, turnover_rate, change_rate, pe_ratio, adj_type, source
- 支持前复权（qfq）数据存储

#### 4.3 ema_breakout_signals表（EMA突破信号）
- 唯一约束：`(market, code, check_date)`
- 字段：market, code, check_date, result_type, is_satisfied, breakout_date, ema10, ema150, close_price, data_rows, result_desc
- 记录每日每只股票的策略判断结果

**主要方法：**
- `init_schema()`: 初始化数据库表结构
- `upsert_stocks()`: 批量插入/更新股票信息
- `upsert_klines()`: 批量插入/更新K线数据（幂等操作）
- `get_klines()`: 查询K线数据
- `upsert_ema_breakout_signal()`: 插入/更新EMA突破信号
- `get_ema_breakout_signals()`: 查询EMA突破信号
- `prune_old_klines()`: 清理过期数据（保留近5年）

---

### 5. **每日数据同步模块 (daily_job.py)**
**职责：** 定时同步股票列表和K线数据到数据库

**核心函数：**
- `run_once()`: 执行一次完整同步
  - 同步股票列表
  - 增量同步K线数据
  - 执行EMA突破策略检查
  - 保存信号到数据库
  
- `run_loop()`: 循环执行（支持定时任务）

- `sync_single_stock()`: 同步单个股票数据
  - 支持通过 `--stock-code` 参数指定
  - 独立的单股票同步逻辑
  - 高内聚，低耦合

- `check_and_save_ema_breakout()`: EMA突破检查并入库
  - 从数据库获取K线数据
  - 执行策略分析
  - 立即写入数据库

**同步策略：**
1. 优先使用本地缓存 (`cache/kline_data`)
2. 缓存未覆盖时按缺口区间调用外部数据源
3. 支持增量同步（仅拉取新数据）
4. 自动清理5年前的历史数据

**数据源优先级：**
1. 本地缓存（Parquet/CSV文件）
2. AKShare
3. YFinance
4. Futu OpenAPI

---

### 6. **K线数据获取器模块 (kline_fetcher.py)**
**职责：** 抽象多数据源K线获取，实现fallback机制

**核心类：**

#### 6.1 KlineFetcherBase（抽象基类）
定义了所有数据获取器的接口：
- `fetch()`: 获取K线数据（抽象方法）
- `get_name()`: 返回数据源名称（抽象方法）

#### 6.2 AKShareKlineFetcher
- 支持多个AKShare接口兜底
- 港股接口：`stock_hk_daily`, `stock_hk_hist`, `stock_hk_hist_em`, `stock_zh_ah_daily`
- 美股接口：`stock_us_daily`, `stock_us_hist`, `stock_us_spot_em`
- 内置重试机制（最多2次）
- 随机延迟避免频繁请求

#### 6.3 YFinanceKlineFetcher
- 使用yfinance库获取数据
- 支持港股（代码格式：00700.HK）和美股
- 最多获取5年历史数据

#### 6.4 FutuKlineFetcher
- 使用富途OpenAPI获取K线
- 支持限流（通过RateLimiter）
- 前复权（QFQ）数据

#### 6.5 KlineFetcherFactory（工厂类）
- `create_akshare_fetcher()`: 创建AKShare获取器
- `create_yfinance_fetcher()`: 创建YFinance获取器
- `create_futu_fetcher()`: 创建Futu获取器
- `create_fetcher_chain()`: 创建获取器链（按优先级排序）

#### 6.6 KlineDataManager（数据管理器）
- 整合多数据源和缓存管理
- 支持Parquet和CSV格式缓存
- 自动选择最优数据源
- 缓存有效期检查（最新数据≥昨天）

---

### 7. **K线图绘制模块 (plot_kline.py)**
**职责：** 从数据库查询数据并绘制K线图

**核心函数：**
- `plot_candlestick()`: 绘制蜡烛图
  - 红色表示上涨，绿色表示下跌（中国股市习惯）
  - 绘制影线（最高到最低）
  - 绘制实体（开盘到收盘）

- `plot_volume()`: 绘制成交量柱状图
  - 颜色与K线涨跌一致

- `plot_kline_chart()`: 主绘图函数
  - 从数据库查询数据
  - 组合K线图和成交量图
  - 支持保存到文件或显示窗口

**图表特性：**
- 双图布局（K线 + 成交量）
- 自动日期格式化
- 价格统计信息显示
- 高分辨率导出（300 DPI）

---

### 8. **市场配置模块 (market.py)**
**职责：** 提供市场配置和工具函数

**核心函数：**
- `normalize_market()`: 标准化市场代码（HK/US）
- `market_label()`: 获取市场中文名称
- `parse_markets()`: 解析逗号分隔的市场列表

**配置：**
```python
MARKET_CONFIG = {
    "HK": {"label": "港股"},
    "US": {"label": "美股"},
}
```

---

## 🎨 设计模式详解

### 1. **工厂模式 (Factory Pattern)**

**位置：** `kline_fetcher.py` - `KlineFetcherFactory`

**作用：** 创建不同类型的K线数据获取器，隐藏具体实现细节

**优点：**
- 解耦对象创建和使用
- 支持动态添加新数据源
- 统一的创建接口

**代码示例：**
```python
class KlineFetcherFactory:
    @staticmethod
    def create_fetcher_chain(quote_ctx=None, rate_limiter=None):
        fetchers = []
        
        # 1. AKShare（优先级最高）
        ak_fetcher = KlineFetcherFactory.create_akshare_fetcher()
        if ak_fetcher:
            fetchers.append(ak_fetcher)
        
        # 2. YFinance（第二优先级）
        yf_fetcher = KlineFetcherFactory.create_yfinance_fetcher()
        if yf_fetcher:
            fetchers.append(yf_fetcher)
        
        # 3. Futu（最后fallback）
        futu_fetcher = KlineFetcherFactory.create_futu_fetcher(quote_ctx, rate_limiter)
        if futu_fetcher:
            fetchers.append(futu_fetcher)
        
        return fetchers
```

---

### 2. **策略模式 (Strategy Pattern)**

**位置：** `strategy.py` - EMA突破策略

**作用：** 将策略逻辑封装成独立模块，便于扩展其他技术指标策略

**优点：**
- 策略逻辑与业务逻辑分离
- 易于添加新策略（如MACD、RSI等）
- 符合开闭原则（对扩展开放，对修改封闭）

**代码示例：**
```python
# 策略接口（当前为EMA突破策略）
def check_ema_breakout(df, check_date, ema_short=10, ema_long=150, lookback_days=2):
    # 策略逻辑实现
    pass

# 未来可扩展其他策略
def check_macd_crossover(df, check_date, ...):
    pass

def check_rsi_oversold(df, check_date, ...):
    pass
```

---

### 3. **责任链模式 (Chain of Responsibility Pattern)**

**位置：** `kline_fetcher.py` - 多数据源fallback机制

**作用：** 按优先级尝试多个数据源，直到成功获取数据

**优点：**
- 自动降级，提高系统可用性
- 每个数据源独立处理
- 易于调整优先级顺序

**代码示例：**
```python
class KlineDataManager:
    def get_kline_data(self, stock_code, market, ...):
        # 1. 尝试缓存
        cached_data = self._load_from_cache(stock_code, market)
        if cached_data is not None:
            return cached_data
        
        # 2. 按优先级尝试各数据源
        for fetcher in self.fetchers:  # [AKShare, YFinance, Futu]
            try:
                data = fetcher.fetch(stock_code, market, ...)
                if data is not None:
                    return data
            except Exception:
                continue  # 失败则尝试下一个
        
        return None
```

**责任链：**
1. **本地缓存** → 2. **AKShare** → 3. **YFinance** → 4. **Futu OpenAPI**

---

### 4. **模板方法模式 (Template Method Pattern)**

**位置：** `kline_fetcher.py` - `KlineFetcherBase`

**作用：** 定义算法骨架，具体步骤由子类实现

**优点：**
- 统一的接口规范
- 复用公共逻辑
- 易于维护和测试

**代码示例：**
```python
class KlineFetcherBase(ABC):
    """抽象基类：定义模板方法"""
    
    @abstractmethod
    def fetch(self, stock_code, market, start_date, end_date, max_count):
        """子类必须实现的方法"""
        pass
    
    @abstractmethod
    def get_name(self):
        """子类必须实现的方法"""
        pass

# 具体实现类
class AKShareKlineFetcher(KlineFetcherBase):
    def fetch(self, ...):
        # AKShare特定实现
        pass
    
    def get_name(self):
        return "AKShare"

class YFinanceKlineFetcher(KlineFetcherBase):
    def fetch(self, ...):
        # YFinance特定实现
        pass
    
    def get_name(self):
        return "YFinance"
```

---

### 5. **数据访问对象模式 (DAO Pattern)**

**位置：** `db.py` - `MarketDatabase`

**作用：** 封装所有数据库操作，提供统一的数据访问接口

**优点：**
- 业务逻辑与数据访问分离
- 易于切换数据库（如从MySQL切换到PostgreSQL）
- 集中管理SQL语句

**代码示例：**
```python
class MarketDatabase:
    def __init__(self, config: MySqlConfig):
        self.conn = pymysql.connect(...)
    
    # 股票数据操作
    def upsert_stocks(self, market, stocks, source):
        """插入/更新股票列表"""
        pass
    
    def get_stocks(self, market):
        """查询股票列表"""
        pass
    
    # K线数据操作
    def upsert_klines(self, market, code, df, adj_type, source):
        """插入/更新K线数据"""
        pass
    
    def get_klines(self, market, code, start_date, end_date, adj_type):
        """查询K线数据"""
        pass
    
    # 策略信号操作
    def upsert_ema_breakout_signal(self, ...):
        """插入/更新EMA突破信号"""
        pass
    
    def get_ema_breakout_signals(self, ...):
        """查询EMA突破信号"""
        pass
```

---

### 6. **单例模式的应用（部分）**

**位置：** `main.py` - `RateLimiter`

**作用：** 确保全局只有一个限流器实例，控制API调用频率

**优点：**
- 全局统一限流
- 避免多实例导致的限流失效

**代码示例：**
```python
class RateLimiter:
    def __init__(self, max_requests=60, time_window=30):
        self.max_requests = max_requests
        self.time_window = time_window
        self.request_times = []
        self.lock = threading.Lock()  # 线程安全
    
    def wait_if_needed(self):
        with self.lock:
            # 限流逻辑
            pass
```

---

### 7. **MVC模式（GUI部分）**

**位置：** `main.py` - GUI实现

**组成：**
- **Model（模型）**: `StockScreener`类，处理业务逻辑
- **View（视图）**: Tkinter GUI界面
- **Controller（控制器）**: GUI事件处理函数（`start_screening`, `on_stock_double_click`等）

**代码示例：**
```python
class StockScreener:
    # Model层
    def screen_stocks(self, stocks):
        """业务逻辑：筛选股票"""
        pass
    
    # View层
    def run_gui(self):
        """创建GUI界面"""
        self.root = tk.Tk()
        self.result_tree = ttk.Treeview(...)
        # ...
    
    # Controller层
    def start_screening(self):
        """事件处理：开始筛选按钮"""
        stocks = self.get_stocks()
        filtered = self.screen_stocks(stocks)
        self.update_results(filtered)
    
    def on_stock_double_click(self, tree):
        """事件处理：双击股票显示图表"""
        stock_code = tree.item(selection)['code']
        self.plot_stock_chart(stock_code)
```

---

### 8. **建造者模式的体现**

**位置：** `daily_job.py` - 数据同步流程

**作用：** 分步骤构建完整的数据同步任务

**步骤：**
1. 初始化数据库连接
2. 创建数据获取器链
3. 获取股票列表
4. 同步K线数据（增量）
5. 执行策略分析
6. 保存信号到数据库
7. 清理过期数据

**代码示例：**
```python
def run_once(mysql, markets, use_futu, ...):
    # Step 1: 初始化
    db = MarketDatabase(mysql)
    db.init_schema()
    
    # Step 2: 创建获取器链
    fetchers = KlineFetcherFactory.create_fetcher_chain(quote_ctx)
    
    # Step 3: 获取股票列表
    stocks = db.get_stocks(market)
    
    # Step 4-7: 处理每只股票
    for stock in stocks:
        # 获取K线数据
        df = fetch_kline_data(...)
        # 存储数据
        db.upsert_klines(...)
        # 执行策略
        check_and_save_ema_breakout(db, ...)
        # 清理过期数据
        db.prune_old_klines(...)
```

---

## 🔄 数据流程图

```
用户输入
   ↓
StockScreener (main.py)
   ↓
获取股票列表 (universe.py)
   ↓ [AKShare/Futu]
   ↓
KlineDataManager (kline_fetcher.py)
   ↓ [责任链：Cache → AKShare → YFinance → Futu]
   ↓
计算技术指标 (pandas_ta)
   ↓
策略分析 (strategy.py)
   ↓ [EMA突破检查]
   ↓
数据库存储 (db.py)
   ↓ [MySQL: stocks, kline_daily, ema_breakout_signals]
   ↓
结果展示
   ├─ GUI界面 (Tkinter)
   ├─ CLI输出 (CSV/JSON)
   └─ K线图表 (plot_kline.py + matplotlib)
```

---

## 📊 数据库设计亮点

### 1. **幂等性设计**
- 使用 `ON DUPLICATE KEY UPDATE` 确保重复插入不会报错
- 唯一约束确保数据不重复：
  - `stocks`: `(market, code)`
  - `kline_daily`: `(market, code, trade_date, adj_type)`
  - `ema_breakout_signals`: `(market, code, check_date)`

### 2. **时间戳自动维护**
- `created_at`: 创建时自动填充
- `updated_at`: 更新时自动更新

### 3. **索引优化**
- 主键索引：所有表都有 `id` 主键
- 唯一索引：确保业务唯一性
- 复合索引：优化常见查询（如 `market + code + date`）

### 4. **数据清理策略**
- 自动清理5年前的K线数据（`prune_old_klines`）
- 节省存储空间，保持查询性能

---

## 🚀 系统优势

### 1. **高可用性**
- 多数据源fallback机制
- 本地缓存优先
- 失败重试机制

### 2. **高性能**
- Parquet格式缓存（比CSV快10倍）
- 数据库索引优化
- 限流器避免API封禁

### 3. **易扩展性**
- 工厂模式支持新增数据源
- 策略模式支持新增技术指标
- DAO模式支持切换数据库

### 4. **易维护性**
- 模块化设计，职责清晰
- 代码注释完善
- 类型提示（Type Hints）

### 5. **用户友好**
- GUI和CLI双模式
- 缓存机制减少等待
- 详细的日志和错误提示

---

## 🔧 技术栈

### 核心库
- **数据获取**: akshare, yfinance, futu-api
- **数据处理**: pandas, numpy
- **技术指标**: pandas_ta
- **数据库**: pymysql
- **GUI**: tkinter
- **可视化**: matplotlib
- **存储**: parquet (pyarrow), csv

### 设计原则
- **SOLID原则**
  - 单一职责原则（SRP）：每个模块只负责一个功能
  - 开闭原则（OCP）：对扩展开放，对修改封闭
  - 里氏替换原则（LSP）：子类可替换父类（KlineFetcherBase）
  - 接口隔离原则（ISP）：接口最小化（fetch + get_name）
  - 依赖倒置原则（DIP）：依赖抽象而非具体实现

- **DRY原则（Don't Repeat Yourself）**
  - 统一的数据规范化函数
  - 复用的工具函数（market.py）

- **高内聚、低耦合**
  - 每个模块独立可测试
  - 模块间通过清晰的接口交互

---

## 📝 最佳实践

### 1. **错误处理**
- 所有数据获取都有try-except保护
- 返回None而非抛出异常（容错设计）

### 2. **日志记录**
- 结构化日志（JSONL格式）
- 包含时间戳、状态、原因等信息

### 3. **数据验证**
- 日期格式验证
- 价格数据有效性检查
- 数据量充足性验证

### 4. **缓存策略**
- 文件缓存（Parquet优先，CSV fallback）
- 内存缓存（stocks_data字典）
- 数据库缓存（避免重复查询）

### 5. **并发安全**
- 限流器使用线程锁（threading.Lock）
- GUI使用线程避免阻塞界面

---

## 🎯 总结

这个股票筛选系统是一个**设计优良、架构清晰、可扩展性强**的量化交易辅助工具。它充分运用了多种设计模式，实现了高内聚、低耦合的模块化设计。

**核心设计模式：**
1. ✅ 工厂模式 - 数据获取器创建
2. ✅ 策略模式 - EMA突破策略
3. ✅ 责任链模式 - 多数据源fallback
4. ✅ 模板方法模式 - 抽象基类
5. ✅ DAO模式 - 数据库访问
6. ✅ MVC模式 - GUI架构
7. ✅ 建造者模式 - 数据同步流程

**主要模块：**
1. 主程序模块 (main.py)
2. 股票列表获取模块 (universe.py)
3. 策略模块 (strategy.py)
4. 数据库模块 (db.py)
5. 每日数据同步模块 (daily_job.py)
6. K线数据获取器模块 (kline_fetcher.py)
7. K线图绘制模块 (plot_kline.py)
8. 市场配置模块 (market.py)

系统具备**高可用性、高性能、易扩展、易维护**的特点，适合作为量化交易系统的基础框架。
