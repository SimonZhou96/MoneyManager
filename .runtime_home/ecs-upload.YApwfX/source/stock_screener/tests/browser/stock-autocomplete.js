// Browser API fixtures only: never submit screening or call data providers.
async page => {
  const queries = []
  let releaseOld
  let oldStarted
  const started = new Promise(resolve => { oldStarted = resolve })
  const pending = new Promise(resolve => { releaseOld = resolve })
  const stock = { code: 'HK.00700', name: 'TENCENT' }
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/auth/me') return route.fulfill({ json: { id: 7, username: 'test-analyst', role: 'admin' } })
    if (url.pathname === '/api/stocks/search') {
      const query = url.searchParams.get('q')
      queries.push(query)
      if (query === 'old-query') {
        oldStarted()
        await pending
      }
      return route.fulfill({ json: { results: [query === 'old-query' ? { code: 'HK.00005', name: 'OLD RESULT' } : stock] } })
    }
    return route.fulfill({ json: { chains: [], tasks: [], history: [] } })
  })
  try {
    await page.goto('http://127.0.0.1:5175/')
    const input = page.getByPlaceholder('输入股票名称，如：腾讯 / Apple / 茅台')
    await input.fill('HK.00700')
    await page.locator('.search-item').filter({ hasText: stock.code }).click()
    // Wait beyond the production debounce so an accidental refill search is observable.
    await page.waitForTimeout(700)
    if (queries.length !== 1 || await page.locator('.search-dropdown').isVisible()) {
      throw new Error('Selecting a stock must not search its display name or reopen suggestions')
    }
    await input.blur()
    await input.focus()
    if (await page.locator('.search-dropdown').isVisible()) throw new Error('Refocusing a selected stock must not reopen old suggestions')
    await input.fill('old-query')
    await started
    await input.fill('HK.00700')
    await page.locator('.search-item').filter({ hasText: stock.code }).click()
    const response = page.waitForResponse(res => new URL(res.url()).searchParams.get('q') === 'old-query')
    releaseOld()
    await response
    await page.waitForTimeout(700)
    if (await page.locator('.search-dropdown').isVisible() || await input.inputValue() !== stock.name) {
      throw new Error('A stale response must not reopen suggestions after selection')
    }
    if (queries.filter(query => query === 'HK.00700').length !== 2) throw new Error('Editing a selected stock must allow a fresh search')
    return 'PASS: selection, focus, editing and stale response; no screening/provider calls'
  } finally {
    releaseOld()
    await page.unroute('**/api/**')
  }
}
