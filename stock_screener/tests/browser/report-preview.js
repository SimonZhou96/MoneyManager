// Verify the local preview uses fixtures, never the real report API.
async page => {
  const apiRequests = []
  const observe = request => { if (new URL(request.url()).pathname.startsWith('/api/reports')) apiRequests.push(request.url()) }
  page.on('request', observe)
  try {
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.goto('http://127.0.0.1:5175/?page=reports&mock_reports=1')
    await page.getByRole('status').filter({ hasText: '示例数据预览' }).waitFor()
    const title = '腾讯控股｜宏观政策、公司信息与技术信号观察'
    await page.getByRole('link', { name: title, exact: true }).waitFor()
    await page.screenshot({ path: '.runtime_home/report-services/reports-list-preview.png' })
    await page.getByRole('button', { name: '下一页', exact: true }).click()
    await page.getByText('第 2 / 2 页 · 共 23 篇', { exact: true }).waitFor()
    await page.getByRole('textbox', { name: '报告关键词' }).fill('HK.00700')
    await page.getByRole('button', { name: '搜索报告', exact: true }).click()
    await page.getByRole('link', { name: title, exact: true }).waitFor()
    await page.getByRole('link', { name: title, exact: true }).click()
    await page.getByRole('heading', { name: '市场背景与宏观政策', exact: true }).waitFor()
    await page.getByText('本报告为 MOCK 示例。技术条件模拟通过，仅用于页面展示，不代表真实行情、公司事实或投资建议。', { exact: true }).waitFor()
    const reader = await page.evaluate(() => ({
      headerTop: document.querySelector('.reports-header').getBoundingClientRect().top,
      titleSize: parseFloat(getComputedStyle(document.querySelector('.report-document-header h2')).fontSize),
    }))
    if (reader.headerTop < 0 || reader.titleSize < 24) throw new Error(`Detail must open at the top with a clear title hierarchy: ${JSON.stringify(reader)}`)
    await page.screenshot({ path: '.runtime_home/report-services/reports-detail-preview.png' })
    await page.setViewportSize({ width: 390, height: 844 })
    if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)) throw new Error('Mock detail overflows mobile viewport')
    if (apiRequests.length) throw new Error(`Preview unexpectedly contacted ES-backed API: ${apiRequests.join(', ')}`)
    await page.getByRole('button', { name: '返回报告列表', exact: true }).click()
    await page.getByRole('link', { name: title, exact: true }).waitFor()
    await page.setViewportSize({ width: 1440, height: 1000 })
    return 'PASS: mock preview, 23 examples, pagination/search/detail, explicit disclaimer, mobile, zero report API calls'
  } finally { page.off('request', observe) }
}
