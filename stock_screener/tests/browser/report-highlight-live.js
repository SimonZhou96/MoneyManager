// Read-only browser verification against seeded local ES; do not mock requests.
async page => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('http://127.0.0.1:5175/?page=reports&q=%E8%85%BE%E8%AE%AF')
  await page.locator('.report-card h3 mark').filter({ hasText: '腾讯' }).first().waitFor()
  await page.locator('.report-match-snippet mark').filter({ hasText: '腾讯' }).first().waitFor()
  if (await page.getByText('MOCK · 示例预览', { exact: true }).count()) throw new Error('This test must read ES, not frontend mock')
  await page.screenshot({ path: '.runtime_home/report-services/reports-es-highlight-tencent.png' })
  await page.getByRole('textbox', { name: '报告关键词' }).fill('人工智能')
  await page.getByRole('button', { name: '搜索报告', exact: true }).click()
  await page.locator('.report-match-snippet mark').filter({ hasText: '人工智能' }).first().waitFor()
  if (await page.locator('.report-card h3 mark').count()) throw new Error('Body-only keyword must not falsely highlight title')
  await page.screenshot({ path: '.runtime_home/report-services/reports-es-highlight-body.png' })
  await page.getByRole('link', { name: /^查看报告：/ }).first().click()
  await page.getByRole('heading', { name: '搜索高亮测试', exact: true }).waitFor()
  await page.getByRole('button', { name: '返回报告列表', exact: true }).click()
  await page.locator('.report-match-snippet mark').filter({ hasText: '人工智能' }).first().waitFor()
  await page.setViewportSize({ width: 390, height: 844 })
  if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)) throw new Error('Highlighted results overflow mobile viewport')
  await page.setViewportSize({ width: 1440, height: 1000 })
  return 'PASS: real ES Tencent title/summary/body highlights, body-only AI keyword, detail/back with query, mobile'
}
