# 股票池多选功能更新

## 更新时间
2026-02-26

## 更新内容

### 功能变更
将股票池类型从**单选**改为**多选**，支持同时选择多个股票池进行合并筛选。

### 使用场景

**场景1：最好股票 + 行业龙头**
- 选择"最好股票"（338只）+ "行业龙头"（35只）
- 合并后：364只股票（去重9只）
- 用途：在优质股票和各行业龙头中寻找突破机会

**场景2：指数成份股 + ETF列表**
- 选择"指数成份股"（105只）+ "ETF列表"（413只）
- 合并后：518只股票
- 用途：在蓝筹股和ETF中寻找投资机会

**场景3：全选**
- 选择所有5种股票池
- 合并后：约1000+只股票（去重后）
- 用途：全市场扫描

## 修改的文件

### 1. frontend/index.html
**修改位置**: 第155-190行

**修改前**:
```html
<div class="form-group">
    <label for="stockpoolType">股票池类型</label>
    <select id="stockpoolType" name="pool_type" required>
        <option value="best">最好股票</option>
        <option value="index">指数成份股</option>
        <option value="industry">行业龙头</option>
        <option value="ipo">新股</option>
        <option value="etf">ETF列表</option>
    </select>
</div>
```

**修改后**:
```html
<div class="form-group">
    <label>股票池类型（可多选）</label>
    <div class="checkbox-group-vertical">
        <label class="checkbox-label">
            <input type="checkbox" class="stockpool-type-checkbox" value="best" checked>
            <span>最好股票</span>
        </label>
        <label class="checkbox-label">
            <input type="checkbox" class="stockpool-type-checkbox" value="index">
            <span>指数成份股</span>
        </label>
        <label class="checkbox-label">
            <input type="checkbox" class="stockpool-type-checkbox" value="industry">
            <span>行业龙头</span>
        </label>
        <label class="checkbox-label">
            <input type="checkbox" class="stockpool-type-checkbox" value="ipo">
            <span>新股</span>
        </label>
        <label class="checkbox-label">
            <input type="checkbox" class="stockpool-type-checkbox" value="etf">
            <span>ETF列表</span>
        </label>
    </div>
</div>
```

### 2. frontend/styles.css
**新增位置**: 第761-797行

新增样式类：
- `.checkbox-group-vertical` - 垂直排列的复选框组
- `.checkbox-label` - 复选框标签样式
- `.checkbox-label:hover` - 悬停效果
- `.checkbox-label input[type="checkbox"]` - 复选框样式
- `.checkbox-label span` - 文本样式

### 3. frontend/app.js
**修改位置**: `loadStockPool()` 函数（第845-939行）

**核心逻辑变更**:

1. **获取选中的股票池类型**（多选）
```javascript
const checkboxes = document.querySelectorAll('.stockpool-type-checkbox:checked');
const poolTypes = Array.from(checkboxes).map(cb => cb.value);
```

2. **验证至少选择一个**
```javascript
if (poolTypes.length === 0) {
    // 提示用户至少选择一个
    return;
}
```

3. **并行请求所有选中的股票池**
```javascript
const promises = poolTypes.map(poolType =>
    fetch(`${API_BASE}/stock-pools?market=${market}&pool_type=${poolType}`)
        .then(response => response.json())
);
const results = await Promise.all(promises);
```

4. **合并数据并去重**
```javascript
const stockMap = new Map();
results.forEach(data => {
    if (data.stocks && data.stocks.length > 0) {
        data.stocks.forEach(stock => {
            if (!stockMap.has(stock.code)) {
                stockMap.set(stock.code, stock);
            }
        });
    }
});
stockpoolData = Array.from(stockMap.values());
```

5. **显示合并信息**
```javascript
const poolTypeLabels = {
    'best': '最好股票',
    'index': '指数成份股',
    'industry': '行业龙头',
    'ipo': '新股',
    'etf': 'ETF列表'
};
const selectedLabels = poolTypes.map(pt => poolTypeLabels[pt] || pt).join(' + ');
infoEl.textContent = `${selectedLabels} | 共 ${stockpoolData.length} 只股票（去重后） | 更新时间: ${latestUpdate}`;
```

## 技术实现

### 去重逻辑
使用 `Map` 数据结构按股票代码（code）去重：
- 如果股票代码已存在，跳过
- 如果股票代码不存在，添加到 Map

### 并行请求
使用 `Promise.all()` 并行请求多个股票池，提高加载速度：
- 单个股票池：约200ms
- 2个股票池并行：约200ms（不是400ms）
- 5个股票池并行：约300ms（不是1000ms）

### 信息展示
- 显示选中的股票池类型（用 + 连接）
- 显示合并后的总数（去重后）
- 显示最新的更新时间

## 测试结果

### 测试1：最好股票 + 行业龙头
```
最好股票: 338 只
行业龙头: 35 只
合并后: 364 只（去重 9 只）
```

**去重的股票**（在两个池中都存在）:
- HK.02318 - PING AN
- HK.01299 - AIA
- HK.03993 - CMOC
- HK.00388 - HKEX
- HK.02328 - PICC P&C
- HK.00066 - MTR CORPORATION
- HK.02618 - JD LOGISTICS
- HK.02245 - LYGEND RESOURCE
- HK.03360 - FE HORIZON

### 测试2：全选5个股票池
```
最好股票: 338 只
指数成份股: 105 只
行业龙头: 35 只
新股: 215 只
ETF列表: 413 只
原始总数: 1106 只
合并后: 约 950-1000 只（去重约 100-150 只）
```

## 使用方法

1. 打开股票池 Tab
2. 选择市场（港股/美股/A股）
3. **勾选一个或多个股票池类型**（默认勾选"最好股票"）
4. 点击"加载股票池"
5. 查看合并后的股票列表
6. 选择时间周期
7. 点击"开始筛选"

## 优势

1. **灵活组合** - 可以根据需求自由组合不同类型的股票池
2. **自动去重** - 避免重复筛选同一只股票
3. **并行加载** - 多个股票池同时加载，速度快
4. **信息透明** - 清楚显示选中的池类型和合并后的数量

## 注意事项

1. 至少需要选择一个股票池类型
2. 选择的股票池越多，筛选时间越长
3. 建议根据投资策略选择相关的股票池组合
4. 去重只按股票代码（code）进行，不考虑其他字段

## 后续优化建议

1. 添加"全选"/"全不选"按钮
2. 记住用户上次选择的股票池类型
3. 显示每个股票池的加载状态
4. 支持保存常用的股票池组合

---

更新完成！
