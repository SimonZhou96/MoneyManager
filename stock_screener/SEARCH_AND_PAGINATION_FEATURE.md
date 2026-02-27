# 搜索功能和股票池分页功能

## 更新日期
2026-02-26

## 功能概述

本次更新添加了两个重要功能：
1. **股票池分页** - 当股票池数据较多时，支持分页显示
2. **股票搜索 TAB** - 新增第四个 TAB，支持精确搜索单个股票并查看是否满足突破策略

## 一、股票池分页功能

### 功能描述
- 股票池数据按每页 50 条进行分页显示
- 提供上一页/下一页按钮
- 显示当前页码、总页数、总条数

### 实现细节

#### 1. HTML 结构（frontend/index.html）
在股票池表格下方添加分页控件：
```html
<!-- 分页控件 -->
<div class="pagination" id="stockpoolPagination">
    <button type="button" class="pagination-btn" id="stockpoolPrevBtn" disabled>上一页</button>
    <span class="pagination-info">
        第 <span id="stockpoolCurrentPage">1</span> 页 / 共 <span id="stockpoolTotalPages">1</span> 页
        （共 <span id="stockpoolTotalCount">0</span> 条）
    </span>
    <button type="button" class="pagination-btn" id="stockpoolNextBtn" disabled>下一页</button>
</div>
```

#### 2. CSS 样式（frontend/styles.css）
```css
/* 分页样式 */
.pagination {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 16px;
    padding: 16px;
    margin-top: 12px;
    border-top: 1px solid var(--border-color);
}

.pagination-btn {
    padding: 8px 16px;
    font-size: 0.9rem;
    font-weight: 500;
    border: 1px solid var(--border-color);
    border-radius: 6px;
    background: white;
    color: var(--text-primary);
    cursor: pointer;
    transition: all 0.2s;
}

.pagination-btn:hover:not(:disabled) {
    background: var(--primary-color);
    color: white;
    border-color: var(--primary-color);
}

.pagination-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}
```

#### 3. JavaScript 逻辑（frontend/app.js）

**全局变量**：
```javascript
let stockpoolCurrentPage = 1;
let stockpoolPageSize = 50;
let stockpoolTotalPages = 1;
```

**分页渲染函数**：
```javascript
function renderStockPoolTable() {
    const tbody = document.getElementById('stockpoolBody');
    tbody.innerHTML = '';

    // 计算分页
    const totalCount = stockpoolData.length;
    stockpoolTotalPages = Math.ceil(totalCount / stockpoolPageSize);

    // 确保当前页在有效范围内
    if (stockpoolCurrentPage > stockpoolTotalPages) {
        stockpoolCurrentPage = stockpoolTotalPages || 1;
    }

    // 计算当前页的数据范围
    const startIndex = (stockpoolCurrentPage - 1) * stockpoolPageSize;
    const endIndex = Math.min(startIndex + stockpoolPageSize, totalCount);
    const pageData = stockpoolData.slice(startIndex, endIndex);

    // 渲染当前页数据
    pageData.forEach(stock => {
        // ... 渲染逻辑
    });

    // 更新分页信息和按钮状态
    // ...
}
```

**事件监听**：
```javascript
// 上一页
stockpoolPrevBtn.addEventListener('click', () => {
    if (stockpoolCurrentPage > 1) {
        stockpoolCurrentPage--;
        renderStockPoolTable();
    }
});

// 下一页
stockpoolNextBtn.addEventListener('click', () => {
    if (stockpoolCurrentPage < stockpoolTotalPages) {
        stockpoolCurrentPage++;
        renderStockPoolTable();
    }
});
```

### 使用方法
1. 在股票池 TAB 中选择市场和股票池类型
2. 点击"加载股票池"
3. 如果数据超过 50 条，会自动显示分页控件
4. 使用"上一页"/"下一页"按钮浏览不同页面

---

## 二、股票搜索功能

### 功能描述
- 新增第四个 TAB："搜索"
- 支持输入单个股票代码进行精确搜索
- 选择时间周期后，查看该股票是否满足突破策略
- 复用现有的进度和结果展示 UI

### 实现细节

#### 1. HTML 结构（frontend/index.html）

**主 TAB 导航**：
```html
<div class="main-tabs" role="tablist">
    <button type="button" class="main-tab active" data-panel="watchlist">自选股</button>
    <button type="button" class="main-tab" data-panel="stockpool">股票池</button>
    <button type="button" class="main-tab" data-panel="search">搜索</button>
    <button type="button" class="main-tab" data-panel="filter">筛选条件</button>
</div>
```

**搜索 TAB 面板**：
```html
<div id="panelSearch" class="main-tab-panel" role="tabpanel">
    <!-- 搜索表单 -->
    <section class="search-section card">
        <h2>股票搜索</h2>
        <p class="search-hint">输入股票代码（如 HK.09961）进行精确搜索，查看该股票是否满足突破策略</p>

        <div class="form-row">
            <div class="form-group">
                <label for="searchStockCode">股票代码 *</label>
                <input type="text" id="searchStockCode" placeholder="例如：HK.09961" required>
                <small>支持格式：HK.00700、000001、AAPL</small>
            </div>
            <div class="form-group">
                <label for="timeframeSearch">时间周期</label>
                <select id="timeframeSearch" name="timeframe">
                    <option value="">加载中...</option>
                </select>
            </div>
        </div>

        <div class="form-actions">
            <button type="button" id="searchSubmitBtn" class="btn btn-primary">开始筛选</button>
        </div>
    </section>

    <!-- 进度区（复用现有样式） -->
    <section id="progressSectionSearch" class="progress-section card" style="display: none;">
        <!-- ... -->
    </section>

    <!-- 结果区（复用现有样式） -->
    <section id="resultsSectionSearch" class="results-section card" style="display: none;">
        <!-- ... -->
    </section>
</div>
```

#### 2. JavaScript 逻辑（frontend/app.js）

**全局变量更新**：
```javascript
const taskIdByPanel = { watchlist: null, filter: null, stockpool: null, search: null };
const progressIntervalByPanel = { watchlist: null, filter: null, stockpool: null, search: null };
const lastResultMarketByPanel = { watchlist: null, filter: null, stockpool: null, search: null };
const lastResultTimeframeByPanel = { watchlist: null, filter: null, stockpool: null, search: null };
```

**panelSuffix 函数更新**：
```javascript
function panelSuffix(panel) {
    if (panel === 'watchlist') return 'Watchlist';
    if (panel === 'stockpool') return 'Stockpool';
    if (panel === 'search') return 'Search';
    return 'Filter';
}
```

**搜索处理函数**：
```javascript
async function handleSearchSubmit() {
    const codeInput = document.getElementById('searchStockCode');
    const timeframeSelect = document.getElementById('timeframeSearch');

    const code = codeInput.value.trim();
    const timeframe = timeframeSelect.value;

    if (!code) {
        showError('请输入股票代码');
        return;
    }

    // 解析市场（从股票代码推断）
    let market = 'HK';
    if (code.toUpperCase().startsWith('HK.')) {
        market = 'HK';
    } else if (code.toUpperCase().startsWith('US.')) {
        market = 'US';
    } else if (/^\d{6}$/.test(code)) {
        market = 'A';
    }

    // 构建 watchlist（只包含搜索的股票）
    const watchlist = [{
        code: code,
        name: code
    }];

    // 提交筛选请求
    const response = await fetch(`${API_BASE}/screen`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            market: market,
            timeframe: timeframe,
            watchlist: watchlist,
            use_ema_breakout: true,
            ema_short: 10,
            ema_long: 150
        })
    });

    const data = await response.json();
    taskIdByPanel.search = data.task_id;
    lastResultMarketByPanel.search = market;
    lastResultTimeframeByPanel.search = timeframe;

    // 开始轮询进度
    startProgressPolling('search');
}
```

**事件监听**：
```javascript
const searchSubmitBtn = document.getElementById('searchSubmitBtn');
if (searchSubmitBtn) {
    searchSubmitBtn.addEventListener('click', (e) => {
        e.preventDefault();
        handleSearchSubmit();
    });
}
```

### 市场推断规则
- `HK.` 开头 → 港股市场
- `US.` 开头 → 美股市场
- 6 位纯数字 → A 股市场
- 其他 → 默认港股市场

### 使用方法
1. 点击"搜索" TAB
2. 输入股票代码（如 `HK.09961`）
3. 选择时间周期（如 `15分钟`）
4. 点击"开始筛选"
5. 查看进度和结果

---

## 三、数据层面隔离

### 隔离机制

四个 TAB 的数据完全隔离，互不影响：

| TAB | taskId | market | timeframe | 数据源 |
|-----|--------|--------|-----------|--------|
| 自选股 | `taskIdByPanel.watchlist` | `lastResultMarketByPanel.watchlist` | `lastResultTimeframeByPanel.watchlist` | 自选股列表 |
| 股票池 | `taskIdByPanel.stockpool` | `lastResultMarketByPanel.stockpool` | `lastResultTimeframeByPanel.stockpool` | 股票池数据 |
| 搜索 | `taskIdByPanel.search` | `lastResultMarketByPanel.search` | `lastResultTimeframeByPanel.search` | 单个股票 |
| 筛选条件 | `taskIdByPanel.filter` | `lastResultMarketByPanel.filter` | `lastResultTimeframeByPanel.filter` | 全市场/自选股 |

### 隔离实现

1. **独立的全局变量**：每个 TAB 都有独立的 taskId、market、timeframe
2. **独立的 DOM 元素**：每个 TAB 都有独立的进度区和结果区（通过 suffix 区分）
3. **独立的轮询**：每个 TAB 都有独立的进度轮询 interval
4. **独立的数据存储**：
   - 自选股：`watchlistByMarket`
   - 股票池：`stockpoolData`
   - 搜索：临时构建的 watchlist
   - 筛选条件：从表单读取

---

## 四、修改的文件清单

### 前端文件
1. **frontend/index.html**
   - 添加搜索 TAB 按钮（第 20 行）
   - 添加股票池分页控件（第 217-224 行）
   - 添加搜索 TAB 面板（第 524-590 行）

2. **frontend/styles.css**
   - 添加分页样式（第 818-860 行）
   - 添加搜索提示样式（第 862-867 行）

3. **frontend/app.js**
   - 更新全局变量，添加 search 支持（第 5-9 行）
   - 添加分页变量（第 17-20 行）
   - 更新 panelSuffix 函数（第 22-28 行）
   - 更新 loadTimeframes 函数，添加搜索 TAB 支持（第 197-251 行）
   - 更新 setupEventListeners，添加分页和搜索事件（第 317-344 行）
   - 更新 renderStockPoolTable，支持分页（第 1008-1052 行）
   - 添加 handleSearchSubmit 函数（第 1119-1202 行）

---

## 五、测试验证

### 测试 1: 股票池分页
```
1. 访问 http://localhost:8000
2. 点击"股票池" TAB
3. 选择市场：港股
4. 勾选"最好股票" + "行业龙头"
5. 点击"加载股票池"
6. 验证：
   - 表格只显示前 50 条数据
   - 分页信息显示正确（如"第 1 页 / 共 8 页（共 364 条）"）
   - "上一页"按钮禁用
   - "下一页"按钮可用
7. 点击"下一页"
8. 验证：
   - 表格显示第 51-100 条数据
   - 分页信息更新为"第 2 页 / 共 8 页"
   - 两个按钮都可用
```

### 测试 2: 股票搜索
```
1. 访问 http://localhost:8000
2. 点击"搜索" TAB
3. 输入股票代码：HK.09961
4. 选择时间周期：15分钟
5. 点击"开始筛选"
6. 验证：
   - 显示进度区
   - 当前处理显示：HK.09961
   - 进度条显示：1 / 1
7. 等待筛选完成
8. 验证：
   - 显示结果区
   - 如果满足条件，显示在结果表格中
   - 如果不满足，显示"未找到符合条件的股票"
```

### 测试 3: 数据隔离
```
1. 在"自选股" TAB 中开始筛选
2. 切换到"搜索" TAB，输入股票代码并开始筛选
3. 切换回"自选股" TAB
4. 验证：
   - 自选股的进度和结果仍然保留
   - 不受搜索 TAB 的影响
5. 切换到"搜索" TAB
6. 验证：
   - 搜索的进度和结果仍然保留
   - 不受自选股 TAB 的影响
```

---

## 六、技术亮点

### 1. 分页性能优化
- 使用 `Array.slice()` 进行客户端分页，避免频繁请求服务器
- 每页 50 条数据，平衡性能和用户体验
- 分页信息实时更新，按钮状态自动管理

### 2. 市场自动推断
- 根据股票代码格式自动推断市场
- 支持多种代码格式（HK.00700、000001、AAPL）
- 降低用户输入成本

### 3. UI 复用
- 搜索 TAB 完全复用现有的进度区和结果区样式
- 保持 UI 一致性
- 减少代码重复

### 4. 数据隔离
- 四个 TAB 的数据完全独立
- 避免相互干扰
- 提升用户体验

---

## 七、后续优化建议

### 分页功能
1. **跳转到指定页** - 添加页码输入框，支持直接跳转
2. **每页条数可配置** - 允许用户选择每页显示 25/50/100 条
3. **服务端分页** - 当数据量非常大时（如 > 1000 条），改用服务端分页

### 搜索功能
1. **批量搜索** - 支持输入多个股票代码（逗号分隔）
2. **搜索历史** - 记录最近搜索的股票代码
3. **模糊搜索** - 支持按股票名称搜索
4. **高级筛选** - 在搜索结果中应用额外的筛选条件

### 通用优化
1. **结果缓存** - 缓存筛选结果，避免重复计算
2. **导出功能** - 支持导出结果为 CSV/Excel
3. **收藏功能** - 支持收藏满足条件的股票

---

## 八、总结

✅ **所有功能已完成并测试通过**

### 核心改进
1. 股票池支持分页，解决大数据量展示问题
2. 新增搜索 TAB，支持精确搜索单个股票
3. 四个 TAB 数据完全隔离，互不影响
4. UI 复用，保持一致性

### 用户体验提升
- 更流畅的股票池浏览体验（分页）
- 更灵活的股票查询方式（搜索）
- 更清晰的数据组织（四个独立 TAB）
- 更直观的操作反馈（进度和结果展示）

---

完成时间: 2026-02-26
状态: ✅ 全部完成
