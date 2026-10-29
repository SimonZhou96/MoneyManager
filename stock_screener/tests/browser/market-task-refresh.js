// Run against Vite on 127.0.0.1:5189 with playwright-cli run-code --filename=<this file>.
// All API calls are intercepted; no real screening or provider requests occur.
async page => {
  let requests = 0
  let finishRefresh
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path !== '/api/screening/tasks') {
      await route.fulfill({ json: { tasks: [], rows: [], chains: [] } })
      return
    }
    requests++
    if (requests === 2) await new Promise(resolve => { finishRefresh = resolve })
    await route.fulfill({ json: requests === 3
      ? { ok: false, message: '模拟刷新失败，请重试' }
      : { jobs: [] } })
  })
  try {
    await page.goto('http://127.0.0.1:5189/')
    await page.getByRole('button', { name: '全市场筛选', exact: true }).click()
    const button = page.getByRole('button', { name: '刷新', exact: true })
    await button.waitFor()
    const style = await button.evaluate(element => ({
      background: getComputedStyle(element).backgroundColor,
      height: element.getBoundingClientRect().height,
    }))
    if (style.background !== 'rgb(20, 25, 32)' || style.height < 44) {
      throw new Error('Refresh must use the dark secondary-button theme and a 44px touch target')
    }
    await button.click()
    const pending = page.getByRole('button', { name: '刷新中…', exact: true })
    await pending.waitFor({ timeout: 3000 })
    if (!await pending.isDisabled()) throw new Error('Pending refresh must disable repeated clicks')
    finishRefresh()
    await button.waitFor()
    if (!await button.isEnabled()) throw new Error('Refresh must be re-enabled after success')
    await button.click()
    await page.getByText('模拟刷新失败，请重试', { exact: true }).waitFor()
    if (!await button.isEnabled()) throw new Error('Refresh must be retryable after failure')
    await button.click()
    await page.getByText('模拟刷新失败，请重试', { exact: true }).waitFor({ state: 'hidden' })
    if (requests !== 4) throw new Error(`Expected four task-list requests, got ${requests}`)
    return 'PASS: theme, touch target, pending state, success, failure and retry'
  } finally {
    if (finishRefresh) finishRefresh()
    await page.unroute('**/api/**')
  }
}
