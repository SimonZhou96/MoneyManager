# 股票池筛选功能调试指南

## 问题描述
点击"加载股票池"或"开始筛选"按钮后没有反应

## 已修复的问题

### 1. resetProgress 函数不存在
**位置**: frontend/app.js 第981行
**修复**: 将 `resetProgress('stockpool')` 改为 `updateProgress('stockpool', 0, 0, '-', [], 0)`

## 调试步骤

### 步骤1: 确认API服务器运行
```bash
# 检查服务器是否运行
curl http://localhost:8000/api/stock-pools/types

# 如果没有响应，启动服务器
MYSQL_PASSWORD=123456 python3 -m api.main
```

### 步骤2: 打开浏览器开发者工具
1. 访问 http://localhost:8000
2. 按 F12 打开开发者工具
3. 切换到 Console 标签页
4. 点击"股票池" Tab
5. 勾选一个股票池类型
6. 点击"加载股票池"按钮

### 步骤3: 检查控制台错误
查看是否有以下错误：

#### 错误1: Uncaught ReferenceError: resetProgress is not defined
**原因**: resetProgress 函数不存在
**解决**: 已修复，刷新页面（Ctrl+F5 强制刷新）

#### 错误2: Failed to fetch
**原因**: API服务器未运行或端口不对
**解决**: 启动API服务器

#### 错误3: CORS error
**原因**: 跨域问题
**解决**: 确保从 http://localhost:8000 访问，不是 file:// 协议

#### 错误4: 404 Not Found
**原因**: API路由不存在
**解决**: 检查 api/main.py 是否注册了 stockpool 路由

### 步骤4: 检查网络请求
1. 切换到 Network 标签页
2. 点击"加载股票池"按钮
3. 查看是否有请求发出

**预期请求**:
```
GET /api/stock-pools?market=HK&pool_type=best
```

**预期响应**:
```json
{
  "stocks": [...],
  "total": 338,
  "last_update": {...}
}
```

### 步骤5: 使用调试页面测试
访问 http://localhost:8000/debug.html 进行独立测试

## 常见问题排查

### 问题1: 点击按钮没有任何反应
**可能原因**:
1. JavaScript 语法错误导致整个脚本加载失败
2. 事件监听器未正确绑定
3. 按钮被禁用

**排查方法**:
```javascript
// 在浏览器控制台输入
console.log(typeof loadStockPool);  // 应该输出 "function"
console.log(document.getElementById('stockpoolLoadBtn'));  // 应该输出按钮元素
```

### 问题2: 请求发出但没有响应
**可能原因**:
1. API服务器崩溃
2. 数据库连接失败
3. 股票池数据不存在

**排查方法**:
```bash
# 检查API服务器日志
tail -f /tmp/api_server.log

# 检查数据库
MYSQL_PASSWORD=123456 mysql -u root -p123456 market_data -e "SELECT COUNT(*) FROM stock_pools WHERE market='HK' AND pool_type='best';"
```

### 问题3: 加载成功但开始筛选没反应
**可能原因**:
1. stockpoolData 为空
2. timeframe 未选择
3. 筛选API调用失败

**排查方法**:
```javascript
// 在浏览器控制台输入
console.log(stockpoolData);  // 查看股票池数据
console.log(document.getElementById('timeframeStockpool').value);  // 查看时间周期
```

## 完整测试流程

### 1. 后端测试
```bash
# 测试股票池API
curl "http://localhost:8000/api/stock-pools?market=HK&pool_type=best" | python3 -m json.tool | head -50

# 测试筛选API
curl -X POST http://localhost:8000/api/screen \
  -H "Content-Type: application/json" \
  -d '{
    "market": "HK",
    "timeframe": "1d",
    "watchlist": [{"code": "HK.00700", "name": "腾讯"}],
    "use_ema_breakout": true,
    "ema_short": 10,
    "ema_long": 150
  }'
```

### 2. 前端测试
1. 访问 http://localhost:8000
2. 打开开发者工具（F12）
3. 点击"股票池" Tab
4. 勾选"最好股票"
5. 点击"加载股票池"
6. 观察控制台和网络请求
7. 选择时间周期"1d"
8. 点击"开始筛选"
9. 观察进度区是否显示

### 3. 独立测试页面
访问 http://localhost:8000/debug.html 进行独立功能测试

## 修复后的验证

修复 resetProgress 问题后，请执行以下操作：

1. **强制刷新页面**: Ctrl+F5 (Windows) 或 Cmd+Shift+R (Mac)
2. **清除缓存**: 开发者工具 > Network > Disable cache
3. **重新测试**: 按照完整测试流程操作

## 预期行为

### 加载股票池
1. 点击"加载股票池"按钮
2. 显示"加载中..."
3. 发送API请求
4. 显示股票池信息："最好股票 | 共 338 只股票（去重后） | 更新时间: ..."
5. 显示股票列表表格
6. "开始筛选"按钮变为可用

### 开始筛选
1. 选择时间周期
2. 点击"开始筛选"按钮
3. 显示进度区
4. 显示"准备中..."
5. 开始显示当前处理的股票
6. 进度条更新
7. 显示满足条件的股票
8. 完成后显示结果表格

## 如果问题仍然存在

请提供以下信息：
1. 浏览器控制台的完整错误信息
2. Network 标签页的请求列表截图
3. API服务器日志 (/tmp/api_server.log)
4. 浏览器和版本信息

---

调试完成后，记得：
- 关闭开发者工具的 "Disable cache"
- 验证所有功能正常工作
- 测试多选股票池功能
