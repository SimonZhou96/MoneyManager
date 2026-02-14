// API 基础 URL
const API_BASE = '/api';

// 全局变量
let currentTaskId = null;
let progressInterval = null;
let watchlistByMarket = { HK: [], A: [], US: [] };
let currentWatchlistTab = 'HK';

// 工具函数：解析带单位的数值
function parseNumberWithUnit(value) {
    if (!value || typeof value !== 'string') {
        return null;
    }
    
    value = value.trim();
    
    // 亿/億 = 1e8
    if (value.includes('亿') || value.includes('億')) {
        const num = parseFloat(value.replace(/[亿億]/g, ''));
        return isNaN(num) ? null : num * 1e8;
    }
    
    // 万/萬 = 1e4
    if (value.includes('万') || value.includes('萬')) {
        const num = parseFloat(value.replace(/[万萬]/g, ''));
        return isNaN(num) ? null : num * 1e4;
    }
    
    // 科学计数法或普通数字
    const num = parseFloat(value);
    return isNaN(num) ? null : num;
}

// 工具函数：格式化数字
function formatNumber(num, decimals = 2) {
    if (num === null || num === undefined) return '-';
    if (typeof num === 'number') {
        return num.toFixed(decimals);
    }
    return num;
}

// 工具函数：格式化大数（市值等）
function formatLargeNumber(num) {
    if (num === null || num === undefined) return '-';
    if (num >= 1e8) {
        return (num / 1e8).toFixed(2) + '亿';
    }
    if (num >= 1e4) {
        return (num / 1e4).toFixed(2) + '万';
    }
    return num.toFixed(2);
}

// 初始化页面
async function init() {
    setupEventListeners();
    try {
        await loadMarkets();
    } catch (e) {
        console.error('loadMarkets failed', e);
    }
    try {
        await loadTimeframes();
    } catch (e) {
        console.error('loadTimeframes failed', e);
    }
    try {
        await loadAllWatchlists();
    } catch (e) {
        console.error('loadAllWatchlists failed', e);
    }
}

// 加载市场列表
async function loadMarkets() {
    try {
        const response = await fetch(`${API_BASE}/markets`);
        const data = await response.json();
        
        const select = document.getElementById('market');
        select.innerHTML = '';
        
        data.markets.forEach(market => {
            const option = document.createElement('option');
            option.value = market.value;
            option.textContent = market.label;
            select.appendChild(option);
        });
        
        // 默认选择港股
        select.value = 'HK';
    } catch (error) {
        console.error('加载市场列表失败:', error);
        showError('加载市场列表失败');
    }
}

// 加载自选股（单市场）
async function loadWatchlist(market, forceRefresh = false) {
    const wrap = document.getElementById('watchlistTableWrap');
    const emptyEl = document.getElementById('watchlistEmpty');
    const loadingEl = document.querySelector('.watchlist-content .watchlist-loading');
    const sourceEl = document.getElementById('watchlistSource');
    if (loadingEl) loadingEl.style.display = 'block';
    if (wrap) wrap.style.display = 'none';
    if (emptyEl) emptyEl.style.display = 'none';
    if (sourceEl) sourceEl.textContent = '';
    try {
        const url = `${API_BASE}/watchlist?market=${encodeURIComponent(market)}`;
        const response = await fetch(url);
        if (!response.ok) throw new Error('获取自选股失败');
        const data = await response.json();
        const stocks = Array.isArray(data.stocks) ? data.stocks : (data.markets && data.markets[market] && data.markets[market].stocks) || [];
        watchlistByMarket[market] = stocks;
        if (sourceEl && data.from_cache) sourceEl.textContent = '（来自缓存）';
        if (market === currentWatchlistTab) renderWatchlist(market);
    } catch (err) {
        console.error('加载自选股失败:', err);
        watchlistByMarket[market] = [];
        if (loadingEl) loadingEl.style.display = 'none';
        if (wrap) wrap.style.display = 'none';
        if (emptyEl) { emptyEl.style.display = 'block'; emptyEl.textContent = '加载失败，请检查 Futu OpenD 或稍后重试'; }
        if (sourceEl) sourceEl.textContent = '';
    }
}

async function loadAllWatchlists() {
    for (const m of ['HK', 'A', 'US']) {
        await loadWatchlist(m);
    }
    renderWatchlist(currentWatchlistTab);
}

function renderWatchlist(market) {
    const loadingEl = document.querySelector('.watchlist-content .watchlist-loading');
    const wrap = document.getElementById('watchlistTableWrap');
    const emptyEl = document.getElementById('watchlistEmpty');
    const tbody = document.getElementById('watchlistBody');
    const list = watchlistByMarket[market] || [];
    if (loadingEl) loadingEl.style.display = 'none';
    if (list.length === 0) {
        if (wrap) wrap.style.display = 'none';
        if (emptyEl) { emptyEl.style.display = 'block'; emptyEl.textContent = '暂无自选股数据'; }
        return;
    }
    if (emptyEl) emptyEl.style.display = 'none';
    if (wrap) wrap.style.display = 'block';
    if (tbody) {
        tbody.innerHTML = '';
        list.forEach(s => {
            const tr = document.createElement('tr');
            tr.innerHTML = `<td>${s.code || '-'}</td><td>${s.name || s.code || '-'}</td>`;
            tbody.appendChild(tr);
        });
    }
}

// 加载 timeframe 列表
async function loadTimeframes() {
    try {
        const response = await fetch(`${API_BASE}/timeframes`);
        const data = await response.json();
        
        const select = document.getElementById('timeframe');
        select.innerHTML = '';
        
        data.timeframes.forEach(tf => {
            const option = document.createElement('option');
            option.value = tf.value;
            option.textContent = tf.label;
            select.appendChild(option);
        });
        
        // 默认选择日线
        select.value = '1d';
    } catch (error) {
        console.error('加载 timeframe 列表失败:', error);
        showError('加载 timeframe 列表失败');
    }
}

// 设置事件监听器
function setupEventListeners() {
    const form = document.getElementById('filterForm');
    const resetBtn = document.getElementById('resetBtn');
    const marketSelect = document.getElementById('market');
    
    form.addEventListener('submit', handleSubmit);
    resetBtn.addEventListener('click', handleReset);

    document.querySelectorAll('.watchlist-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.watchlist-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentWatchlistTab = btn.getAttribute('data-market');
            renderWatchlist(currentWatchlistTab);
        });
    });
    document.getElementById('watchlistRefreshBtn').addEventListener('click', () => {
        loadWatchlist(currentWatchlistTab, true);
    });
    marketSelect.addEventListener('change', () => {
        const m = marketSelect.value;
        if (m && watchlistByMarket[m] && watchlistByMarket[m].length === 0) {
            loadWatchlist(m);
        }
    });
}

// 处理表单提交
async function handleSubmit(e) {
    e.preventDefault();
    
    const submitBtn = document.getElementById('submitBtn');
    submitBtn.disabled = true;
    submitBtn.textContent = '启动中...';
    
    try {
        // 收集表单数据
        const formData = new FormData(e.target);
        const params = {
            market: formData.get('market'),
            timeframe: formData.get('timeframe'),
            use_ema_breakout: document.getElementById('useEmaBreakout').checked,
            ema_short: 10,  // 默认值
            ema_long: 150,  // 默认值
            require_profitable: document.getElementById('requireProfitable').checked,
        };
        
        // 解析数值参数
        const marketCapMin = formData.get('market_cap_min');
        if (marketCapMin) {
            params.market_cap_min = parseNumberWithUnit(marketCapMin);
        }
        
        const marketCapMax = formData.get('market_cap_max');
        if (marketCapMax) {
            params.market_cap_max = parseNumberWithUnit(marketCapMax);
        }
        
        const avgVolumeMin = formData.get('avg_daily_volume_min');
        if (avgVolumeMin) {
            params.avg_daily_volume_min = parseNumberWithUnit(avgVolumeMin);
        }
        
        const priceMin = formData.get('price_min');
        if (priceMin) {
            params.price_min = parseFloat(priceMin);
        }
        
        const priceMax = formData.get('price_max');
        if (priceMax) {
            params.price_max = parseFloat(priceMax);
        }
        
        const peMin = formData.get('pe_min');
        if (peMin) {
            params.pe_min = parseFloat(peMin);
        }
        
        const peMax = formData.get('pe_max');
        if (peMax) {
            params.pe_max = parseFloat(peMax);
        }

        const screenWatchlistOnly = document.getElementById('screenWatchlistOnly').checked;
        if (screenWatchlistOnly) {
            const market = params.market;
            const list = watchlistByMarket[market] || [];
            if (list.length === 0) {
                showError('当前市场自选股为空，请先刷新自选股或取消勾选「仅筛选自选股」');
                submitBtn.disabled = false;
                submitBtn.textContent = '开始筛选';
                return;
            }
            params.watchlist = list;
        }
        
        // 发起筛选请求
        const response = await fetch(`${API_BASE}/screen`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(params),
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || '启动筛选失败');
        }
        
        const data = await response.json();
        currentTaskId = data.task_id;
        
        // 显示进度区
        showProgressSection();
        
        // 开始轮询进度
        startProgressPolling();
        
    } catch (error) {
        console.error('启动筛选失败:', error);
        showError(error.message || '启动筛选失败');
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = '开始筛选';
    }
}

// 处理重置
function handleReset() {
    const form = document.getElementById('filterForm');
    form.reset();
    
    // 恢复默认值
    document.getElementById('market').value = 'HK';
    document.getElementById('timeframe').value = '1d';
    document.getElementById('useEmaBreakout').checked = true;
    document.getElementById('requireProfitable').checked = true;
    document.getElementById('screenWatchlistOnly').checked = false;

    // 隐藏进度和结果区
    hideProgressSection();
    hideResultsSection();
}

// 显示进度区
function showProgressSection() {
    const section = document.getElementById('progressSection');
    section.style.display = 'block';
    
    // 重置进度
    updateProgress(0, 0, '-', [], 0);
    
    const statusText = document.getElementById('statusText');
    statusText.textContent = '准备中...';
    statusText.className = 'progress-status';
    
    // 隐藏结果区
    hideResultsSection();
}

// 隐藏进度区
function hideProgressSection() {
    const section = document.getElementById('progressSection');
    section.style.display = 'none';
    
    // 停止轮询
    stopProgressPolling();
}

// 显示结果区
function showResultsSection() {
    const section = document.getElementById('resultsSection');
    section.style.display = 'block';
}

// 隐藏结果区
function hideResultsSection() {
    const section = document.getElementById('resultsSection');
    section.style.display = 'none';
}

// 更新进度
function updateProgress(completed, total, currentStock, passedStocks, passedCount) {
    const progressBar = document.getElementById('progressBar');
    const progressText = document.getElementById('progressText');
    const currentStockElem = document.getElementById('currentStock');
    
    const percentage = total > 0 ? (completed / total) * 100 : 0;
    progressBar.style.width = `${percentage}%`;
    progressText.textContent = `${completed} / ${total}`;
    currentStockElem.textContent = currentStock || '-';
    
    // 更新满足条件的股票列表
    updatePassedStocksList(passedStocks || [], passedCount || 0);
}

// 更新满足条件的股票列表
function updatePassedStocksList(passedStocks, passedCount) {
    const countElem = document.getElementById('passedCount');
    const listElem = document.getElementById('passedStocksList');
    
    countElem.textContent = passedCount;
    
    if (!passedStocks || passedStocks.length === 0) {
        listElem.innerHTML = '<div class="empty-message">暂无满足条件的股票</div>';
        return;
    }
    
    listElem.innerHTML = '';
    
    passedStocks.forEach(stock => {
        const item = document.createElement('div');
        item.className = 'passed-stock-item';
        item.innerHTML = `
            <span class="stock-code">${stock.code}</span>
            <span class="stock-name">${stock.name}</span>
        `;
        listElem.appendChild(item);
    });
}

// 开始轮询进度
function startProgressPolling() {
    if (progressInterval) {
        clearInterval(progressInterval);
    }
    
    // 立即执行一次
    pollProgress();
    
    // 每 5 秒轮询一次
    progressInterval = setInterval(pollProgress, 5000);
}

// 停止轮询进度
function stopProgressPolling() {
    if (progressInterval) {
        clearInterval(progressInterval);
        progressInterval = null;
    }
}

// 轮询进度
async function pollProgress() {
    if (!currentTaskId) return;
    
    try {
        const response = await fetch(`${API_BASE}/progress/${currentTaskId}`);
        
        if (!response.ok) {
            throw new Error('获取进度失败');
        }
        
        const data = await response.json();
        
        // 更新进度
        const currentStock = data.current_stock_code 
            ? `${data.current_stock_code} (${data.current_stock_name || ''})`
            : '-';
        
        updateProgress(
            data.completed_count, 
            data.total_count, 
            currentStock,
            data.passed_stocks || [],
            data.passed_count || 0
        );
        
        const statusText = document.getElementById('statusText');
        
        if (data.status === 'completed') {
            // 筛选完成
            stopProgressPolling();
            statusText.textContent = `✓ 筛选完成！共找到 ${data.passed_count || 0} 只符合条件的股票`;
            statusText.className = 'progress-status success';
            
            // 加载结果
            await loadResults(currentTaskId);
            
        } else if (data.status === 'failed') {
            // 筛选失败
            stopProgressPolling();
            statusText.textContent = '✗ 筛选失败';
            statusText.className = 'progress-status error';
            
        } else {
            // 运行中
            statusText.textContent = `筛选进行中... 已找到 ${data.passed_count || 0} 只符合条件的股票`;
            statusText.className = 'progress-status';
        }
        
    } catch (error) {
        console.error('轮询进度失败:', error);
    }
}

// 加载结果
async function loadResults(taskId) {
    try {
        const response = await fetch(`${API_BASE}/results/${taskId}?passed_only=true`);
        
        if (!response.ok) {
            throw new Error('获取结果失败');
        }
        
        const data = await response.json();
        
        // 显示结果
        displayResults(data.results);
        
    } catch (error) {
        console.error('加载结果失败:', error);
        showError('加载结果失败');
    }
}

// 显示结果
function displayResults(results) {
    showResultsSection();
    
    const summary = document.getElementById('resultsSummary');
    summary.textContent = `共找到 ${results.length} 只符合条件的股票`;
    
    const tbody = document.getElementById('resultsBody');
    tbody.innerHTML = '';
    
    if (results.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; padding: 40px; color: var(--text-secondary);">未找到符合条件的股票</td></tr>';
        return;
    }
    
    results.forEach(stock => {
        const row = document.createElement('tr');
        
        row.innerHTML = `
            <td>${stock.code}</td>
            <td>${stock.name || '-'}</td>
            <td>${stock.sector || stock.industry || '-'}</td>
            <td>${formatLargeNumber(stock.market_cap)}</td>
            <td>${formatNumber(stock.close_price)}</td>
            <td>${formatNumber(stock.pe_ratio)}</td>
        `;
        
        tbody.appendChild(row);
    });
}

// 显示错误
function showError(message) {
    alert(`错误: ${message}`);
}

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', init);
