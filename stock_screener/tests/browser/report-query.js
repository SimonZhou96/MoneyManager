// Browser-only API fixtures: no ES writes, provider calls, or screening jobs.
async page => {
  let failure = false
  const report = { report_id: 'single_stock-fixture', task_id: 'fixture', task_type: 'single_stock',
    title: '腾讯 · 浏览器验收报告', summary: '宏观政策、公司信息和技术依据', markets: ['HK'],
    codes: ['HK.00700'], names: ['腾讯'], passed_count: 1, generated_at: '2026-10-29T07:00:00Z',
    content_markdown: '# 浏览器测试正文\n\n| 指标 | 值 |\n| --- | --- |\n| 示例 | 1 |\n\n[危险链接](javascript:alert(1))\n\n<img src=x onerror=alert(1) />\n\n[来源链接](https://example.com/source)' }
  const highlights = {
    title: [[{ text: '腾讯', matched: true }, { text: ' · 浏览器验收报告', matched: false }]],
    content_text: [[{ text: '<img src=x onerror=alert(1)>政策支持', matched: false }, { text: '腾讯', matched: true }, { text: '发展', matched: false }]],
  }
  let seenSearch = false
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url())
    if (url.pathname.startsWith('/api/reports')) {
      if (failure) return route.fulfill({ json: { ok: false, message: 'ES 暂不可用，请重试' } })
      if (url.pathname !== '/api/reports') return route.fulfill({ json: report })
      if (url.searchParams.get('q') === '腾讯') seenSearch = true
      return route.fulfill({ json: { reports: url.searchParams.get('q') === '不存在' ? [] : [{ ...report, ...(url.searchParams.get('q') ? { highlights } : {}) }],
        total: url.searchParams.get('q') === '不存在' ? 0 : 21, page: Number(url.searchParams.get('page') || 1), page_size: 20 } })
    }
    await route.fulfill({ json: { chains: [], tasks: [] } })
  })
  try {
    await page.goto('http://127.0.0.1:5175/?mock_reports=1')
    const nav = page.getByRole('button', { name: '报告查询', exact: true })
    await nav.waitFor({ timeout: 3000 })
    await nav.click()
    await page.getByRole('link', { name: report.title, exact: true }).waitFor({ timeout: 3000 })
    await page.getByRole('textbox', { name: '报告关键词' }).fill('腾讯')
    await page.getByRole('button', { name: '搜索报告', exact: true }).click()
    await page.waitForURL('**q=*')
    if (!seenSearch) throw new Error('Search must pass q to report API')
    await page.locator('.report-card h3 mark').filter({ hasText: '腾讯' }).waitFor()
    await page.locator('.report-match-snippet mark').filter({ hasText: '腾讯' }).waitFor()
    if (await page.locator('.report-card img, .report-card script').count()) throw new Error('Highlight content must render as plain text, never HTML')
    if (!(await page.locator('.report-match-snippet').textContent()).includes('<img src=x')) throw new Error('Untrusted markup must remain visible text')
    await page.getByRole('button', { name: '下一页', exact: true }).click()
    await page.waitForURL('**report_page=2*')
    await page.getByRole('link', { name: report.title, exact: true }).click()
    await page.getByRole('heading', { name: '浏览器测试正文', exact: true }).waitFor()
    if (await page.locator('.report-markdown img, .report-markdown script, .report-markdown a[href^="javascript:"]').count()) {
      throw new Error('Markdown must block raw HTML, external images and javascript links')
    }
    if (await page.locator('.report-markdown table').count() !== 1) throw new Error('GFM table missing')
    await page.reload()
    await page.getByRole('heading', { name: '浏览器测试正文', exact: true }).waitFor()
    await page.getByRole('button', { name: '返回报告列表', exact: true }).click()
    if (!page.url().includes('report_page=2') || !page.url().includes('q=')) throw new Error('Back must retain query/page')
    await page.getByRole('textbox', { name: '报告关键词' }).fill('不存在')
    await page.getByRole('button', { name: '搜索报告', exact: true }).click()
    await page.getByRole('heading', { name: '没有找到相关报告', exact: true }).waitFor()
    failure = true
    await page.getByRole('button', { name: '刷新报告', exact: true }).click()
    await page.getByRole('alert').filter({ hasText: 'ES 暂不可用' }).waitFor()
    failure = false
    await page.getByRole('button', { name: '重试', exact: true }).click()
    await page.getByRole('heading', { name: '没有找到相关报告', exact: true }).waitFor()
    await page.setViewportSize({ width: 390, height: 844 })
    if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)) {
      throw new Error('Report page must not overflow mobile viewport')
    }
    return 'PASS: sidebar, real API contract, search, pagination, deep link, back, safe Markdown, empty/error/retry, mobile'
  } finally {
    await page.unroute('**/api/**')
  }
}
