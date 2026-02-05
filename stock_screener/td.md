# Futu OpenAPI 接口清单（港股 EMA / HMA 项目）

---

## 一、基础连接

### 1. 连接 Futu OpenD
- 使用 py-futu-api
- 本地运行 FutuOpenD
- 通过 QuoteContext(host, port) 建立连接

用途：
- 所有行情、板块、K 线接口的前置条件

---

## 二、港股股票池（Universe）

### 2. 获取港股全量股票列表

API：
- QuoteContext.get_stock_basicinfo()

参数：
- market = Market.HK
- stock_type = SecurityType.STOCK

返回：
- 股票代码（如 HK.00700）
- 股票名称
- 股票类型

用途：
- 构建港股 Universe
- 批量筛选目标股票

---

## 三、板块 / 行业数据

### 3. 获取板块列表

API：
- QuoteContext.get_plate_list()

参数：
- market = Market.HK
- plate_class = PlateClass.INDUSTRY（行业）
- plate_class = PlateClass.CONCEPT（概念）

返回：
- 板块 code
- 板块名称

用途：
- 获取所有可用行业 / 概念板块

---

### 4. 获取板块成分股

API：
- QuoteContext.get_plate_stock()

参数：
- plate_code

返回：
- 板块内股票列表

用途：
- 构建 stock_code → 板块 映射
- 按板块分组展示筛选结果

---

## 四、历史行情（日线）

### 5. 获取历史日线 K 线数据

API：
- QuoteContext.get_history_kline()

参数：
- code = HK.00700
- ktype = KLType.K_DAY
- max_count >= 800
- autype = AuType.QFQ（前复权）

返回：
- time_key
- open / high / low / close
- volume

用途：
- 计算 EMA10 / EMA150
- 计算 HMA40 / HMA200 / HMA600
- 绘制日线蜡烛图

---

## 五、（可选）辅助接口

### 6. 获取单只股票基本信息（可选）

API：
- QuoteContext.get_stock_basicinfo()

参数：
- market = Market.HK
- stock_type = SecurityType.STOCK

用途：
- 获取股票名称、状态
- UI 显示用

---

## 六、明确不需要的接口（当前阶段）

- 实时行情订阅接口
- 分钟 / Tick 级 K 线接口
- 交易 / 下单接口
- 资金 / 持仓接口

---

## 七、最小接口集合（MVP）

仅用以下 4 个接口即可跑完整功能：

1. QuoteContext.get_stock_basicinfo  
2. QuoteContext.get_plate_list  
3. QuoteContext.get_plate_stock  
4. QuoteContext.get_history_kline  

---

## 八、接口调用顺序建议

1. get_stock_basicinfo → 构建港股股票池  
2. get_plate_list / get_plate_stock → 构建板块映射  
3. get_history_kline → 批量拉日线  
4. 本地计算 EMA / HMA  
5. 筛选 EMA10 昨日上穿 EMA150  
6. 点击股票 → 再次调用 get_history_kline 画图

# 技术指标计算：Python 包与函数清单（本地计算）

> 输入数据统一为 pandas.Series（收盘价 close）
> 所有计算均不依赖 Futu API

---

## 一、推荐 Python 包

### 1. pandas（必须）
- 用于 EMA
- 内置 ewm，行为与主流交易软件一致

安装：
```bash
pip install pandas
```

ema10  = close.ewm(span=10, adjust=False).mean()

ema150 = close.ewm(span=150, adjust=False).mean()

import pandas_ta as ta

hma40  = ta.hma(close, length=40)
hma200 = ta.hma(close, length=200)
hma600 = ta.hma(close, length=600)


