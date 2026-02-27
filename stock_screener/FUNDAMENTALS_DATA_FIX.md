# 筛选结果基本面数据缺失问题修复

## 问题描述

用户报告两个问题：
1. **进度中的满足条件股票**：没有显示选中原因（策略标签）
2. **筛选结果表格**：板块、市值、价格、市盈率等字段显示为空

## 问题根因

### 问题 1: 进度中缺少策略标签
**根因**：`/api/progress/{task_id}` 接口返回的 `passed_stocks` 只包含 `code` 和 `name`，没有 `satisfied_strategies` 字段。

### 问题 2: 结果中基本面数据为空
**根因**：数据库 `stocks` 表中的基本面数据本身就是 NULL。

通过查询数据库验证：
```sql
SELECT code, name, sector, industry, market_cap, pe_ratio
FROM stocks
WHERE market='HK' AND code IN ('HK.00941', 'HK.09961', 'HK.00700');

-- 结果：所有基本面字段都是 NULL
code         name           sector  industry  market_cap  pe_ratio
HK.00700     腾讯控股       NULL    NULL      NULL        NULL
HK.00941     中国移动       NULL    NULL      NULL        NULL
HK.09961     携程集团－Ｓ   NULL    NULL      NULL        NULL
```

**深层原因**：
1. `universe.py` 中的 `fetch_stock_list_akshare()` 函数只尝试获取 `market_cap` 和 `pe_ratio`，没有获取 `sector` 和 `industry`
2. AKShare API 返回的数据中可能没有这些字段，或者列名不匹配
3. 即使有 `market_cap` 和 `pe_ratio`，列名匹配失败也会导致数据为 NULL

## 已完成的修复

### 修复 1: 增强 AKShare 数据获取逻辑 ✅

**文件**: `universe.py`

**修改位置**: 第 101-177 行

**修改内容**:
1. 添加了更多列名匹配规则，提高数据获取成功率
2. 新增 sector（板块）和 industry（行业）字段的获取
3. 新增 pb_ratio（市净率）和 price（价格）字段的获取
4. 改进了市值单位转换逻辑（支持"亿"单位自动转换）
5. 添加了数据验证，过滤无效值（如 '-', '--', 'nan' 等）

**关键代码**:
```python
# 扩展的列名匹配
sector_col = _find_column(data.columns, ["板块", "sector", "所属板块", "行业板块", "一级行业"])
industry_col = _find_column(data.columns, ["行业", "industry", "所属行业", "细分行业", "二级行业"])
market_cap_col = _find_column(data.columns, ["总市值", "流通市值", "market_cap", "市值", "总市值(元)", "总市值(亿)"])
pe_col = _find_column(data.columns, ["市盈率", "市盈率-动态", "pe", "pe_ratio", "市盈率(动态)", "动态市盈率"])
pb_col = _find_column(data.columns, ["市净率", "pb", "pb_ratio", "市净率(动态)"])
price_col = _find_column(data.columns, ["最新价", "现价", "price", "close", "收盘价", "最新"])

# 数据验证和添加
if sector_col and sector_col in row:
    sector = str(row[sector_col]).strip()
    if sector and sector not in ['-', '--', 'nan', 'None', '']:
        item["sector"] = sector
```

**效果**:
- ✅ 代码已更新，支持获取更多基本面字段
- ⚠️ 但 AKShare 的 `stock_hk_spot_em()` API 本身不提供这些字段，只提供价格数据

### 修复 2: 创建基本面数据更新工具 ✅

**文件**: `update_fundamentals.py`（新建）

**功能**:
- 使用 Futu API 批量获取股票基本面数据
- 支持 HK/US/A 三个市场
- 自动更新数据库中的 sector, industry, market_cap, pe_ratio, pb_ratio 字段
- 支持批量处理（每批 200 只股票）
- 包含限速和错误处理

**使用方法**:
```bash
# 更新港股基本面数据
MYSQL_PASSWORD=123456 python3 update_fundamentals.py --market HK

# 更新美股基本面数据
MYSQL_PASSWORD=123456 python3 update_fundamentals.py --market US

# 更新 A 股基本面数据
MYSQL_PASSWORD=123456 python3 update_fundamentals.py --market A
```

**前提条件**:
- Futu OpenD 必须正在运行（127.0.0.1:11111）
- 已安装 futu-api: `pip install futu-api`

### 修复 3: 进度 API 返回策略标签 ✅

**文件**：`api/routes/screen.py`

**修改位置**：第 137-179 行

**修改内容**：
```python
# 按 task_id 查询已通过筛选的股票（避免同一天多任务结果混淆）
sql = """
    SELECT code, name, filter_details
    FROM screening_results
    WHERE task_id=%s AND is_passed=1
    ORDER BY code
"""
with db.conn.cursor() as cursor:
    cursor.execute(sql, (task["task_id"],))
    rows = cursor.fetchall() or []

db.close()

# 提取满足的策略
strategy_name_map = {
    'EMABreakoutStrategizer': 'EMA突破',
    'RSIOversoldStrategizer': 'RSI超卖',
    'RSIOverboughtStrategizer': 'RSI超买',
}

passed_stocks = []
for row in rows:
    code = row[0]
    name = row[1] or code
    filter_details_raw = row[2]

    # 解析 filter_details 提取满足的策略
    satisfied_strategies = []
    if filter_details_raw:
        try:
            filter_details = json.loads(filter_details_raw) if isinstance(filter_details_raw, str) else filter_details_raw
            if isinstance(filter_details, list):
                for detail in filter_details:
                    if detail.get('result') == 'pass' and detail.get('filter_name') in strategy_name_map:
                        satisfied_strategies.append(strategy_name_map[detail['filter_name']])
        except Exception:
            pass

    passed_stocks.append({
        "code": code,
        "name": name,
        "satisfied_strategies": satisfied_strategies
    })
```

**效果**：
- ✅ 进度中的满足条件股票现在会显示策略标签（如"EMA突破"、"RSI超卖"）
- ✅ 前端已有显示逻辑，无需修改

### 修复 4: 改进错误日志 ✅

**文件**：`api/screen_service.py`

**修改位置**：第 157-174 行

**修改内容**：
```python
# 自选股补全基本面（从 stocks 表按 code 查）
try:
    fund_list = db.get_stocks_by_codes(market, [s["code"] for s in stocks], include_fundamentals=True)
    fund_by_code = {r["code"]: r for r in fund_list}
    for s in stocks:
        f = fund_by_code.get(s["code"])
        if f:
            s["sector"] = s["sector"] or f.get("sector")
            s["industry"] = s["industry"] or f.get("industry")
            s["market_cap"] = s["market_cap"] if s.get("market_cap") is not None else f.get("market_cap")
            s["pe_ratio"] = s["pe_ratio"] if s.get("pe_ratio") is not None else f.get("pe_ratio")
            s["pb_ratio"] = s["pb_ratio"] if s.get("pb_ratio") is not None else f.get("pb_ratio")
    if verbose:
        print(f"✓ 已从数据库补全 {len([s for s in stocks if s.get('market_cap')])} 只股票的基本面数据")
except Exception as e:
    if verbose:
        print(f"⚠️  补全基本面数据失败: {e}")
    pass
```

**效果**：
- ✅ 现在会输出补全成功的股票数量
- ✅ 如果补全失败，会输出错误信息

## 待执行：更新数据库基本面数据

### 使用 update_fundamentals.py 工具

**步骤 1: 确保 Futu OpenD 正在运行**
```bash
# 检查 Futu OpenD 是否运行
lsof -i :11111
```

**步骤 2: 运行更新脚本**
```bash
# 更新港股基本面数据
MYSQL_PASSWORD=123456 python3 update_fundamentals.py --market HK
```

**预期输出**:
```
============================================================
更新 HK 市场股票基本面数据
============================================================

✓ 找到 2737 只HK市场股票
✓ 已连接 Futu OpenD

开始获取基本面数据...
  正在获取第 1-200 只股票的基本面数据...
  正在获取第 201-400 只股票的基本面数据...
  ...
✓ 成功获取 2500 只股票的基本面数据

开始更新数据库...
✓ 成功更新 2500 只股票的基本面数据

============================================================
完成
============================================================
```

**步骤 3: 验证数据**
```bash
MYSQL_PASSWORD=123456 mysql -u root -p123456 market_data -e "
SELECT code, name, sector, industry, market_cap, pe_ratio
FROM stocks
WHERE market='HK' AND sector IS NOT NULL
LIMIT 5;
"
```

## 备选方案（如果 Futu API 不可用）

### 方案 A: 使用 AKShare 个股接口（较慢）

AKShare 提供了 `stock_individual_basic_info_hk_xq` 接口可以获取单个股票的详细信息，但需要逐个查询：

```python
import akshare as ak

# 获取单个港股的基本信息
df = ak.stock_individual_basic_info_hk_xq(symbol="00700")
print(df)
```

**缺点**: 需要逐个查询，速度很慢（2700+ 只股票需要很长时间）

### 方案 B: 手动导入关键股票数据

对于常用的重点股票，可以手动更新数据库：

```sql
-- 腾讯控股
UPDATE stocks SET
    sector = '科技',
    industry = '互联网服务',
    market_cap = 3500000000000,
    pe_ratio = 25.5,
    pb_ratio = 3.8
WHERE code = 'HK.00700';

-- 中国移动
UPDATE stocks SET
    sector = '电信',
    industry = '移动通信',
    market_cap = 1800000000000,
    pe_ratio = 12.3,
    pb_ratio = 1.2
WHERE code = 'HK.00941';
```

## 原有方案文档（已过时）

以下内容保留作为参考，但已被上述修复替代：

### ~~方案 1: 改进 AKShare 数据获取~~（已实现但 API 不支持）
1. 添加更多列名匹配规则
2. 尝试获取 sector 和 industry 字段

**示例代码**：
```python
def fetch_stock_list_akshare(market: str) -> List[dict]:
    # ... 现有代码 ...

    # 添加更多列名匹配
    code_col = _find_column(data.columns, ["代码", "code", "symbol", "ticker"])
    name_col = _find_column(data.columns, ["名称", "name", "中文名称", "英文名称"])
    sector_col = _find_column(data.columns, ["板块", "sector", "所属板块", "行业板块"])
    industry_col = _find_column(data.columns, ["行业", "industry", "所属行业", "细分行业"])
    market_cap_col = _find_column(data.columns, ["总市值", "流通市值", "market_cap", "市值", "总市值(元)"])
    pe_col = _find_column(data.columns, ["市盈率", "市盈率-动态", "pe", "pe_ratio", "市盈率(动态)"])
    price_col = _find_column(data.columns, ["最新价", "现价", "price", "close", "收盘价"])

    # ... 现有代码 ...

    for _, row in data.iterrows():
        # ... 现有代码 ...

        item = {"code": code, "name": name}

        # 添加 sector 和 industry
        if sector_col and sector_col in row:
            sector = str(row[sector_col]).strip()
            if sector and sector != '-' and sector != '--':
                item["sector"] = sector

        if industry_col and industry_col in row:
            industry = str(row[industry_col]).strip()
            if industry and industry != '-' and industry != '--':
                item["industry"] = industry

        # 现有的 market_cap 和 pe_ratio
        if market_cap_col and market_cap_col in row:
            item["market_cap"] = _safe_float(row[market_cap_col])

        if pe_col and pe_col in row:
            item["pe_ratio"] = _safe_float(row[pe_col])

        # 添加 price
        if price_col and price_col in row:
            item["price"] = _safe_float(row[price_col])

        stocks.append(item)
```

### 方案 2: 使用 Futu API 获取基本面数据

**优点**：
- Futu API 提供完整的基本面数据
- 数据质量高，更新及时

**缺点**：
- 需要 Futu OpenD 运行
- API 调用有频率限制

**示例代码**：
```python
def fetch_fundamentals_futu(quote_ctx, market: str, codes: List[str]) -> Dict[str, dict]:
    """
    从 Futu API 获取股票基本面数据

    Returns:
        {code: {sector, industry, market_cap, pe_ratio, ...}}
    """
    try:
        from futu import OpenQuoteContext

        result = {}
        for code in codes:
            ret, data = quote_ctx.get_stock_basicinfo(market, [code])
            if ret == 0 and not data.empty:
                row = data.iloc[0]
                result[code] = {
                    "sector": row.get("sector"),
                    "industry": row.get("industry"),
                    "market_cap": row.get("market_cap"),
                    "pe_ratio": row.get("pe_ratio"),
                }
        return result
    except Exception as e:
        print(f"获取基本面数据失败: {e}")
        return {}
```

### 方案 3: 临时解决方案 - 手动导入数据

如果上述方案都不可行，可以手动导入基本面数据：

```sql
-- 更新港股基本面数据（示例）
UPDATE stocks SET
    sector = '科技',
    industry = '互联网',
    market_cap = 3500000000000,
    pe_ratio = 25.5
WHERE code = 'HK.00700';

UPDATE stocks SET
    sector = '电信',
    industry = '移动通信',
    market_cap = 1800000000000,
    pe_ratio = 12.3
WHERE code = 'HK.00941';
```

## 测试验证

### 测试 1: 验证进度中的策略标签

```bash
# 1. 启动筛选
curl -X POST http://localhost:8000/api/screen \
  -H "Content-Type: application/json" \
  -d '{
    "market": "HK",
    "timeframe": "15m",
    "watchlist": [{"code": "HK.00700", "name": "腾讯控股"}]
  }'

# 2. 获取进度
curl http://localhost:8000/api/progress/{task_id}

# 3. 验证返回的 passed_stocks 包含 satisfied_strategies
# 预期结果：
{
  "passed_stocks": [
    {
      "code": "HK.00700",
      "name": "腾讯控股",
      "satisfied_strategies": ["EMA突破", "RSI超卖"]
    }
  ]
}
```

### 测试 2: 验证基本面数据

```bash
# 1. 检查数据库中的数据
MYSQL_PASSWORD=123456 mysql -u root -p123456 market_data -e "
SELECT code, name, sector, industry, market_cap, pe_ratio
FROM stocks
WHERE market='HK'
LIMIT 10;
"

# 2. 如果数据为 NULL，需要重新导入股票列表
# 或者使用方案 1/2/3 修复数据获取逻辑
```

## 总结

### 已完成 ✅
1. **进度 API 返回策略标签** - 修改 `api/routes/screen.py`，解析 filter_details 提取满足的策略
2. **增强 AKShare 数据获取** - 修改 `universe.py`，添加更多字段和列名匹配规则
3. **改进错误日志** - 修改 `api/screen_service.py`，输出补全成功/失败信息
4. **创建更新工具** - 新建 `update_fundamentals.py`，使用 Futu API 批量更新基本面数据

### 待执行 ⏳
1. **运行 update_fundamentals.py** - 使用 Futu API 更新数据库中的基本面数据
   - 前提：Futu OpenD 必须运行
   - 命令：`MYSQL_PASSWORD=123456 python3 update_fundamentals.py --market HK`

### 用户体验提升
- ✅ 进度中显示策略标签（如"EMA突破"、"RSI超卖"）
- ⏳ 结果表格显示完整基本面数据（板块、市值、价格、市盈率等）- 需要先运行更新工具

### 技术说明
- **AKShare 限制**: `stock_hk_spot_em()` 只提供实时价格，不提供基本面数据
- **解决方案**: 使用 Futu API 的 `get_stock_basicinfo()` 批量获取基本面数据
- **数据流**: Futu API → update_fundamentals.py → stocks 表 → screen_service.py → 筛选结果

---

修复时间: 2026-02-26
状态: 代码修复完成 ✅，数据更新待执行 ⏳
