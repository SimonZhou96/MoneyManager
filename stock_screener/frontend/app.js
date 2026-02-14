// API 基础 URL
const API_BASE = '/api';

// 全局变量
const taskIdByPanel = { watchlist: null, filter: null };
const progressIntervalByPanel = { watchlist: null, filter: null };
// 每个 panel 当前结果/进度对应的市场与时间周期，用于点击股票时请求图表使用正确参数
const lastResultMarketByPanel = { watchlist: null, filter: null };
const lastResultTimeframeByPanel = { watchlist: null, filter: null };
let watchlistByMarket = { HK: [], A: [], US: [] };
let currentWatchlistTab = 'HK';
let currentMarket = 'HK';
let currentTimeframe = '1d';
let chartInstance = null;

// panel 对应 DOM ID 后缀：'watchlist' | 'filter' -> 'Watchlist' | 'Filter'
function panelSuffix(panel) {
    return panel === 'watchlist' ? 'Watchlist' : 'Filter';
}

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
    try {
        const response = await fetch(`${API_BASE}/last-result`);
        if (!response.ok) return;
        const data = await response.json();
        if (data.task_id) {
            currentMarket = data.market || currentMarket;
            currentTimeframe = data.timeframe || currentTimeframe;
            const marketEl = document.getElementById('market');
            const timeframeEl = document.getElementById('timeframe');
            if (marketEl && data.market) marketEl.value = data.market;
            if (timeframeEl && data.timeframe) timeframeEl.value = data.timeframe;
            taskIdByPanel.filter = data.task_id;
            await loadResults(data.task_id, 'filter');
        }
    } catch (e) {
        console.error('load last result failed', e);
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

// 加载 timeframe 列表（筛选条件 + 自选股两处共用）
async function loadTimeframes() {
    try {
        const response = await fetch(`${API_BASE}/timeframes`);
        const data = await response.json();
        
        const select = document.getElementById('timeframe');
        const selectWatchlist = document.getElementById('timeframeWatchlist');
        if (select) {
            select.innerHTML = '';
            data.timeframes.forEach(tf => {
                const option = document.createElement('option');
                option.value = tf.value;
                option.textContent = tf.label;
                select.appendChild(option);
            });
            select.value = '1d';
        }
        if (selectWatchlist) {
            selectWatchlist.innerHTML = '';
            data.timeframes.forEach(tf => {
                const option = document.createElement('option');
                option.value = tf.value;
                option.textContent = tf.label;
                selectWatchlist.appendChild(option);
            });
            selectWatchlist.value = '1d';
        }
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

    document.querySelectorAll('.main-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            const panelId = btn.getAttribute('data-panel') === 'watchlist' ? 'panelWatchlist' : 'panelFilter';
            document.querySelectorAll('.main-tab').forEach(b => {
                b.classList.remove('active');
                b.setAttribute('aria-selected', 'false');
            });
            btn.classList.add('active');
            btn.setAttribute('aria-selected', 'true');
            document.querySelectorAll('.main-tab-panel').forEach(p => p.classList.remove('active'));
            const panel = document.getElementById(panelId);
            if (panel) panel.classList.add('active');
        });
    });

    const resultFilterApplyBtnWatchlist = document.getElementById('resultFilterApplyBtnWatchlist');
    if (resultFilterApplyBtnWatchlist) {
        resultFilterApplyBtnWatchlist.addEventListener('click', () => applyResultFilter('watchlist'));
    }
    const resultFilterApplyBtnFilter = document.getElementById('resultFilterApplyBtnFilter');
    if (resultFilterApplyBtnFilter) {
        resultFilterApplyBtnFilter.addEventListener('click', () => applyResultFilter('filter'));
    }

    const selfSubmitBtn = document.getElementById('selfSubmitBtn');
    if (selfSubmitBtn) {
        selfSubmitBtn.addEventListener('click', (e) => {
            e.preventDefault();
            handleSelfSubmit();
        });
    }

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

// 从筛选条件表单构建请求参数；marketOverride 有值时用作 market（自选股 Tab 用当前自选股市场 + 自选股时间周期）
function buildScreenParamsFromForm(marketOverride) {
    const form = document.getElementById('filterForm');
    const formData = new FormData(form);
    const timeframeEl = marketOverride !== undefined
        ? document.getElementById('timeframeWatchlist')
        : document.getElementById('timeframe');
    const params = {
        market: marketOverride !== undefined ? marketOverride : formData.get('market'),
        timeframe: (timeframeEl && timeframeEl.value) ? timeframeEl.value : formData.get('timeframe'),
        use_ema_breakout: document.getElementById('useEmaBreakout').checked,
        ema_short: 10,
        ema_long: 150,
        require_profitable: document.getElementById('requireProfitable').checked,
    };
    const marketCapMin = formData.get('market_cap_min');
    if (marketCapMin) params.market_cap_min = parseNumberWithUnit(marketCapMin);
    const marketCapMax = formData.get('market_cap_max');
    if (marketCapMax) params.market_cap_max = parseNumberWithUnit(marketCapMax);
    const avgVolumeMin = formData.get('avg_daily_volume_min');
    if (avgVolumeMin) params.avg_daily_volume_min = parseNumberWithUnit(avgVolumeMin);
    const priceMin = formData.get('price_min');
    if (priceMin) params.price_min = parseFloat(priceMin);
    const priceMax = formData.get('price_max');
    if (priceMax) params.price_max = parseFloat(priceMax);
    const peMin = formData.get('pe_min');
    if (peMin) params.pe_min = parseFloat(peMin);
    const peMax = formData.get('pe_max');
    if (peMax) params.pe_max = parseFloat(peMax);
    const screenWatchlistOnly = document.getElementById('screenWatchlistOnly').checked;
    if (screenWatchlistOnly) {
        const market = params.market;
        const list = watchlistByMarket[market] || [];
        if (list.length === 0) return { params: null, error: '当前市场自选股为空，请先刷新自选股或取消勾选「仅筛选自选股」' };
        params.watchlist = list;
    }
    return { params, error: null };
}

// 使用已构建的 params 发起筛选请求并进入进度轮询；panel 为 'watchlist' 或 'filter'
async function runScreeningWithParams(params, submitBtn, panel) {
    submitBtn.disabled = true;
    const originalText = submitBtn.textContent;
    submitBtn.textContent = '启动中...';
    try {
        const response = await fetch(`${API_BASE}/screen`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params),
        });
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || '启动筛选失败');
        }
        const data = await response.json();
        taskIdByPanel[panel] = data.task_id;
        currentMarket = params.market || 'HK';
        currentTimeframe = params.timeframe || '1d';
        lastResultMarketByPanel[panel] = params.market || 'HK';
        lastResultTimeframeByPanel[panel] = params.timeframe || '1d';
        showProgressSection(panel);
        startProgressPolling(panel);
    } catch (error) {
        console.error('启动筛选失败:', error);
        showError(error.message || '启动筛选失败');
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = originalText;
    }
}

// 处理筛选条件 Tab 表单提交
async function handleSubmit(e) {
    e.preventDefault();
    const { params, error } = buildScreenParamsFromForm();
    if (error) {
        showError(error);
        return;
    }
    await runScreeningWithParams(params, document.getElementById('submitBtn'), 'filter');
}

// 自选股 Tab「开始筛选」：使用当前自选股市场 + 强制只筛当前自选股列表（不依赖「仅筛选自选股」勾选）
async function handleSelfSubmit() {
    const { params, error } = buildScreenParamsFromForm(currentWatchlistTab);
    if (error) {
        showError(error);
        return;
    }
    const list = watchlistByMarket[params.market] || [];
    if (list.length === 0) {
        showError('当前市场自选股为空，请先刷新自选股');
        return;
    }
    params.watchlist = list;
    await runScreeningWithParams(params, document.getElementById('selfSubmitBtn'), 'watchlist');
}

// 处理重置（仅影响筛选条件 Tab）
function handleReset() {
    const form = document.getElementById('filterForm');
    form.reset();
    
    document.getElementById('market').value = 'HK';
    document.getElementById('timeframe').value = '1d';
    document.getElementById('useEmaBreakout').checked = true;
    document.getElementById('requireProfitable').checked = true;
    document.getElementById('screenWatchlistOnly').checked = false;

    hideProgressSection('filter');
    hideResultsSection('filter');
    stopProgressPolling('filter');
}

// 显示进度区（按 panel）
function showProgressSection(panel) {
    const suffix = panelSuffix(panel);
    const section = document.getElementById('progressSection' + suffix);
    if (section) section.style.display = 'block';
    updateProgress(panel, 0, 0, '-', [], 0);
    const statusText = document.getElementById('statusText' + suffix);
    if (statusText) {
        statusText.textContent = '准备中...';
        statusText.className = 'progress-status';
    }
    hideResultsSection(panel);
}

// 隐藏进度区（按 panel）
function hideProgressSection(panel) {
    const suffix = panelSuffix(panel);
    const section = document.getElementById('progressSection' + suffix);
    if (section) section.style.display = 'none';
    stopProgressPolling(panel);
}

// 显示结果区（按 panel）
function showResultsSection(panel) {
    const suffix = panelSuffix(panel);
    const section = document.getElementById('resultsSection' + suffix);
    if (section) section.style.display = 'block';
}

// 隐藏结果区（按 panel）
function hideResultsSection(panel) {
    const suffix = panelSuffix(panel);
    const section = document.getElementById('resultsSection' + suffix);
    if (section) section.style.display = 'none';
}

// 更新进度（按 panel）
function updateProgress(panel, completed, total, currentStock, passedStocks, passedCount) {
    const suffix = panelSuffix(panel);
    const progressBar = document.getElementById('progressBar' + suffix);
    const progressText = document.getElementById('progressText' + suffix);
    const currentStockElem = document.getElementById('currentStock' + suffix);
    const percentage = total > 0 ? (completed / total) * 100 : 0;
    if (progressBar) progressBar.style.width = `${percentage}%`;
    if (progressText) progressText.textContent = `${completed} / ${total}`;
    if (currentStockElem) currentStockElem.textContent = currentStock || '-';
    updatePassedStocksList(panel, passedStocks || [], passedCount || 0);
}

// 更新满足条件的股票列表（按 panel）
function updatePassedStocksList(panel, passedStocks, passedCount) {
    const suffix = panelSuffix(panel);
    const countElem = document.getElementById('passedCount' + suffix);
    const listElem = document.getElementById('passedStocksList' + suffix);
    if (countElem) countElem.textContent = passedCount;
    if (!listElem) return;
    if (!passedStocks || passedStocks.length === 0) {
        listElem.innerHTML = '<div class="empty-message">暂无满足条件的股票</div>';
        return;
    }
    listElem.innerHTML = '';
    passedStocks.forEach(stock => {
        const item = document.createElement('div');
        item.className = 'passed-stock-item';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'stock-code-btn';
        btn.textContent = stock.code;
        btn.title = '点击查看 K 线图';
        const market = lastResultMarketByPanel[panel];
        const timeframe = lastResultTimeframeByPanel[panel];
        btn.addEventListener('click', () => showStockChart(stock.code, stock.name, { market, timeframe }));
        item.appendChild(btn);
        const nameSpan = document.createElement('span');
        nameSpan.className = 'stock-name';
        nameSpan.textContent = ` ${stock.name || ''}`;
        item.appendChild(nameSpan);
        listElem.appendChild(item);
    });
}

// 开始轮询进度（按 panel）
function startProgressPolling(panel) {
    if (progressIntervalByPanel[panel]) {
        clearInterval(progressIntervalByPanel[panel]);
    }
    pollProgress(panel);
    progressIntervalByPanel[panel] = setInterval(() => pollProgress(panel), 5000);
}

// 停止轮询进度（按 panel）
function stopProgressPolling(panel) {
    if (progressIntervalByPanel[panel]) {
        clearInterval(progressIntervalByPanel[panel]);
        progressIntervalByPanel[panel] = null;
    }
}

// 轮询进度（按 panel）
async function pollProgress(panel) {
    const taskId = taskIdByPanel[panel];
    if (!taskId) return;
    const suffix = panelSuffix(panel);
    try {
        const response = await fetch(`${API_BASE}/progress/${taskId}`);
        if (!response.ok) throw new Error('获取进度失败');
        const data = await response.json();
        const currentStock = data.current_stock_code
            ? `${data.current_stock_code} (${data.current_stock_name || ''})`
            : '-';
        if (data.market) {
            currentMarket = data.market;
            lastResultMarketByPanel[panel] = data.market;
        }
        if (data.timeframe) {
            currentTimeframe = data.timeframe;
            lastResultTimeframeByPanel[panel] = data.timeframe;
        }
        updateProgress(
            panel,
            data.completed_count,
            data.total_count,
            currentStock,
            data.passed_stocks || [],
            data.passed_count || 0
        );
        const statusText = document.getElementById('statusText' + suffix);
        if (!statusText) return;
        if (data.status === 'completed') {
            stopProgressPolling(panel);
            statusText.textContent = `✓ 筛选完成！共找到 ${data.passed_count || 0} 只符合条件的股票`;
            statusText.className = 'progress-status success';
            await loadResults(taskId, panel);
        } else if (data.status === 'failed') {
            stopProgressPolling(panel);
            statusText.textContent = '✗ 筛选失败';
            statusText.className = 'progress-status error';
        } else {
            statusText.textContent = `筛选进行中... 已找到 ${data.passed_count || 0} 只符合条件的股票`;
            statusText.className = 'progress-status';
        }
    } catch (error) {
        console.error('轮询进度失败:', error);
    }
}

// 加载结果（按 panel 展示）
async function loadResults(taskId, panel) {
    try {
        const response = await fetch(`${API_BASE}/results/${taskId}?passed_only=true`);
        if (!response.ok) throw new Error('获取结果失败');
        const data = await response.json();
        taskIdByPanel[panel] = taskId;
        if (data.market) {
            currentMarket = data.market;
            lastResultMarketByPanel[panel] = data.market;
        }
        if (data.timeframe) {
            currentTimeframe = data.timeframe;
            lastResultTimeframeByPanel[panel] = data.timeframe;
        }
        displayResults(data.results, panel);
    } catch (error) {
        console.error('加载结果失败:', error);
        showError('加载结果失败');
    }
}

// filter_name 到简短中文标签的映射（选中原因）
const FILTER_NAME_LABELS = {
    EMABreakoutStrategizer: 'EMA突破',
    RSIOversoldStrategizer: 'RSI超卖',
    RSIOverboughtStrategizer: 'RSI超买',
    MarketCapFilter: '市值',
    PEFilter: '市盈率',
    PriceFilter: '价格',
    AvgDailyVolumeFilter: '日均量',
    ProfitabilityFilter: '盈利',
};

function getReasonLabel(filterName) {
    return FILTER_NAME_LABELS[filterName] || filterName || '';
}

function renderReasonTags(filterDetails) {
    if (!Array.isArray(filterDetails)) return '';
    const passed = filterDetails.filter(d => d && d.result === 'pass');
    if (passed.length === 0) return '-';
    return passed
        .map(d => {
            const label = getReasonLabel(d.filter_name);
            if (!label) return '';
            return `<span class="reason-tag">${label}</span>`;
        })
        .filter(Boolean)
        .join('');
}

// 应用结果筛选（下钻，按 panel）
async function applyResultFilter(panel) {
    const taskId = taskIdByPanel[panel];
    if (!taskId) {
        showError('暂无筛选结果，请先完成一次筛选');
        return;
    }
    const suffix = panelSuffix(panel);
    const params = new URLSearchParams({ passed_only: 'true' });
    const sector = document.getElementById('resultFilterSector' + suffix)?.value?.trim();
    const industry = document.getElementById('resultFilterIndustry' + suffix)?.value?.trim();
    const marketCapMin = document.getElementById('resultFilterMarketCapMin' + suffix)?.value?.trim();
    const marketCapMax = document.getElementById('resultFilterMarketCapMax' + suffix)?.value?.trim();
    const peMin = document.getElementById('resultFilterPeMin' + suffix)?.value?.trim();
    const peMax = document.getElementById('resultFilterPeMax' + suffix)?.value?.trim();
    const priceMin = document.getElementById('resultFilterPriceMin' + suffix)?.value?.trim();
    const priceMax = document.getElementById('resultFilterPriceMax' + suffix)?.value?.trim();
    if (sector) params.set('sector', sector);
    if (industry) params.set('industry', industry);
    const capMinNum = marketCapMin ? parseNumberWithUnit(marketCapMin) : null;
    const capMaxNum = marketCapMax ? parseNumberWithUnit(marketCapMax) : null;
    if (capMinNum != null) params.set('market_cap_min', String(capMinNum));
    if (capMaxNum != null) params.set('market_cap_max', String(capMaxNum));
    if (peMin) params.set('pe_min', peMin);
    if (peMax) params.set('pe_max', peMax);
    if (priceMin) params.set('close_price_min', priceMin);
    if (priceMax) params.set('close_price_max', priceMax);
    try {
        const response = await fetch(`${API_BASE}/results/${taskId}?${params}`);
        if (!response.ok) throw new Error('获取结果失败');
        const data = await response.json();
        displayResults(data.results, panel);
    } catch (err) {
        console.error('结果筛选失败:', err);
        showError(err.message || '结果筛选失败');
    }
}

// 显示结果（按 panel）
function displayResults(results, panel) {
    showResultsSection(panel);
    const suffix = panelSuffix(panel);
    const summary = document.getElementById('resultsSummary' + suffix);
    const tbody = document.getElementById('resultsBody' + suffix);
    if (summary) summary.textContent = `共找到 ${results.length} 只符合条件的股票`;
    if (!tbody) return;
    tbody.innerHTML = '';
    if (results.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; padding: 40px; color: var(--text-secondary);">未找到符合条件的股票</td></tr>';
        return;
    }
    const chartMarket = lastResultMarketByPanel[panel];
    const chartTimeframe = lastResultTimeframeByPanel[panel];
    results.forEach(stock => {
        const row = document.createElement('tr');
        const codeBtn = document.createElement('button');
        codeBtn.type = 'button';
        codeBtn.className = 'stock-code-btn';
        codeBtn.textContent = stock.code;
        codeBtn.title = '点击查看 K 线图';
        codeBtn.addEventListener('click', () => showStockChart(stock.code, stock.name, { market: chartMarket, timeframe: chartTimeframe }));
        const reasonTagsHtml = renderReasonTags(stock.filter_details);
        row.innerHTML = `
            <td></td>
            <td>${stock.name || '-'}</td>
            <td>${stock.sector || stock.industry || '-'}</td>
            <td>${formatLargeNumber(stock.market_cap)}</td>
            <td>${formatNumber(stock.close_price)}</td>
            <td>${formatNumber(stock.pe_ratio)}</td>
            <td class="reason-tags-cell">${reasonTagsHtml}</td>
        `;
        row.querySelector('td:first-child').appendChild(codeBtn);
        tbody.appendChild(row);
    });
}

// 显示错误
function showError(message) {
    alert(`错误: ${message}`);
}

// 解析股票代码（去掉市场前缀，如 HK.00700 -> 00700）
function parseStockCodeForApi(code) {
    if (!code || typeof code !== 'string') return code;
    const s = code.trim();
    if (s.toUpperCase().startsWith('HK.')) return s.slice(3);
    if (s.toUpperCase().startsWith('US.')) return s.slice(3);
    if (s.includes('.') && /^[0-9]+\.[A-Z]{2}$/i.test(s)) return s.split('.')[0];
    return s;
}

// 显示股票 K 线图；可选第三参数 { market, timeframe } 为点击来源对应的市场与周期，避免双 Tab 错用
async function showStockChart(code, name, options) {
    const modal = document.getElementById('chartModal');
    const titleEl = document.getElementById('chartModalTitle');
    const rsiBadge = document.getElementById('chartRsiBadge');
    const chartContainer = document.getElementById('chartContainer');
    
    const apiCode = parseStockCodeForApi(code);
    const market = (options && options.market) || currentMarket || 'HK';
    const timeframe = (options && options.timeframe) || currentTimeframe || '1d';
    
    modal.style.display = 'flex';
    titleEl.textContent = `${name || code} - 加载中...`;
    rsiBadge.textContent = 'RSI: -';
    chartContainer.innerHTML = '<div class="loading" style="margin: 40px auto;"></div>';
    
    try {
        const params = new URLSearchParams({ code: apiCode, market, timeframe });
        if (name) params.set('name', name);
        const response = await fetch(`${API_BASE}/chart?${params}`);
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || '获取图表数据失败');
        }
        const data = await response.json();
        
        titleEl.textContent = `${data.name} (${timeframe})`;
        rsiBadge.textContent = `RSI: ${data.latest_rsi != null ? data.latest_rsi : '-'}`;
        
        chartContainer.innerHTML = '';
        
        if (!window.LightweightCharts) {
            throw new Error('图表库未加载');
        }
        
        const chartDiv = document.createElement('div');
        chartDiv.style.width = '100%';
        chartDiv.style.minWidth = '400px';
        chartDiv.style.height = '450px';
        chartContainer.appendChild(chartDiv);
        // 使用容器宽度，避免刚 append 时 clientWidth 为 0 导致图表不绘制
        const chartWidth = Math.max(chartDiv.clientWidth, chartContainer.clientWidth, 400);
        
        if (chartInstance) {
            chartInstance.remove();
            chartInstance = null;
        }
        
        const chartWidget = window.LightweightCharts.createChart(chartDiv, {
            layout: { textColor: '#64748b', background: { type: 'solid', color: '#ffffff' } },
            grid: { vertLines: { color: '#e2e8f0' }, horzLines: { color: '#e2e8f0' } },
            width: chartWidth,
            height: 450,
            rightPriceScale: { borderColor: '#e2e8f0' },
            leftPriceScale: { borderColor: '#e2e8f0', scaleMargins: { top: 0.9, bottom: 0 } },
            timeScale: { borderColor: '#e2e8f0', timeVisible: true, secondsVisible: false },
        });
        
        chartInstance = chartWidget;
        
        // 统一 time 格式：LightweightCharts 日线用 'yyyy-MM-dd'，带 T 的用 UTC 秒
        function chartTime(t) {
            if (t == null) return t;
            if (typeof t === 'string' && t.indexOf('T') !== -1) {
                const ms = new Date(t).getTime();
                return isNaN(ms) ? t : Math.floor(ms / 1000);
            }
            return t;
        }
        
        const ohlc = data.data.map(d => ({
            time: chartTime(d.time),
            open: d.open,
            high: d.high,
            low: d.low,
            close: d.close,
        }));
        const ema10Data = data.data.filter(d => d.ema10 != null).map(d => ({ time: chartTime(d.time), value: d.ema10 }));
        const ema150Data = data.data.filter(d => d.ema150 != null).map(d => ({ time: chartTime(d.time), value: d.ema150 }));
        const rsiData = data.data.filter(d => d.rsi != null).map(d => ({ time: chartTime(d.time), value: d.rsi }));
        
        const candlestickSeries = chartWidget.addCandlestickSeries({
            upColor: '#ef4444',
            downColor: '#10b981',
            borderUpColor: '#ef4444',
            borderDownColor: '#10b981',
        });
        candlestickSeries.setData(ohlc);
        
        const ema10Series = chartWidget.addLineSeries({ color: '#2563eb', lineWidth: 2, title: 'EMA10' });
        ema10Series.setData(ema10Data);
        
        const ema150Series = chartWidget.addLineSeries({ color: '#f59e0b', lineWidth: 2, title: 'EMA150' });
        ema150Series.setData(ema150Data);
        
        const rsiSeries = chartWidget.addLineSeries({
            color: '#8b5cf6',
            lineWidth: 2,
            title: 'RSI',
            priceScaleId: 'left',
        });
        rsiSeries.setData(rsiData);
        chartWidget.priceScale('left').applyOptions({ scaleMargins: { top: 0.9, bottom: 0 } });
        
        chartWidget.timeScale().fitContent();
        
        const resizeHandler = () => {
            const w = Math.max(chartDiv.clientWidth, chartContainer.clientWidth, 400);
            chartWidget.applyOptions({ width: w });
        };
        window.addEventListener('resize', resizeHandler);
        chartInstance._resizeHandler = resizeHandler;
        requestAnimationFrame(resizeHandler);
        
    } catch (err) {
        console.error('加载图表失败:', err);
        chartContainer.innerHTML = `<div class="empty-message" style="padding: 40px;">加载失败: ${err.message}</div>`;
    }
}

// 关闭图表弹窗
function closeChartModal() {
    document.getElementById('chartModal').style.display = 'none';
    if (chartInstance) {
        try {
            if (chartInstance._resizeHandler) {
                window.removeEventListener('resize', chartInstance._resizeHandler);
            }
            chartInstance.remove();
        } catch (e) {}
        chartInstance = null;
    }
}

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', () => {
    init();
    document.getElementById('chartModalClose').addEventListener('click', closeChartModal);
    document.querySelector('.chart-modal-backdrop').addEventListener('click', closeChartModal);
});
