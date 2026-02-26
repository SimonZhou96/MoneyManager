# 股票池筛选功能 - 快速开始

## 功能说明

股票池筛选功能已完成实现，允许你将预定义的股票池投入选股器进行策略筛选。

## 使用步骤

### 1. 确保股票池数据已准备

如果还没有股票池数据，先运行：

```bash
cd /Users/simon/Documents/GitHub/MoneyManager/stock_screener
MYSQL_PASSWORD=123456 python3 fetch_stock_pools.py --market HK --pools all
```

### 2. 启动 API 服务器

```bash
MYSQL_PASSWORD=123456 python3 -m api.main
```

### 3. 打开浏览器

访问：http://localhost:8000

### 4. 使用股票池筛选

1. 点击顶部的 **"股票池"** Tab
2. 选择市场（港股/美股/A股）
3. 选择股票池类型：
   - **最好股票** - 338只优质股票（市值≥50亿，价格≥5，PE≥5，成交量≥2000万）
   - **指数成份股** - 168只主要指数成份股
   - **行业龙头** - 35只各行业前5名股票
   - **新股** - 最近两年上市的股票
   - **ETF列表** - 413只ETF和REIT
4. 点击 **"加载股票池"** 按钮
5. 查看加载的股票列表
6. 选择时间周期（1d/1w/1m）
7. 点击 **"开始筛选"** 按钮
8. 等待筛选完成，查看满足 EMA 突破策略的股票

## 功能特点

✅ 5种预定义股票池，覆盖不同投资策略
✅ 实时进度显示，可查看当前处理的股票
✅ 支持结果二次筛选（板块、行业、市值、PE、价格）
✅ 点击股票代码可查看K线图
✅ 复用现有筛选逻辑，保证一致性

## 架构说明

- **后端 API**: `api/routes/stockpool.py` - 提供3个REST接口
- **前端界面**: `frontend/index.html` - 新增"股票池" Tab
- **前端逻辑**: `frontend/app.js` - 股票池加载和筛选逻辑
- **数据存储**: MySQL `stock_pools` 表

## 详细文档

完整的功能说明、API文档、使用场景请参考：
- `STOCKPOOL_SCREENING_FEATURE.md` - 完整功能文档

## 故障排查

**问题：点击"加载股票池"后提示"该股票池暂无数据"**
解决：运行 `fetch_stock_pools.py` 获取数据

**问题：API服务器启动失败**
解决：确保已安装依赖 `pip install fastapi uvicorn`

**问题：筛选进度卡住不动**
解决：检查 Futu OpenD 是否正常运行

## 下一步

建议设置定时任务每天更新股票池数据：

```bash
# 每天收盘后更新（周一到周五 17:00）
0 17 * * 1-5 cd /Users/simon/Documents/GitHub/MoneyManager/stock_screener && MYSQL_PASSWORD=123456 python3 fetch_stock_pools.py --market HK --pools all
```

---

实现完成时间：2026-02-26
