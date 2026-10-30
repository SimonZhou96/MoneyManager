// Isolated API fixtures; never create real accounts or screening tasks.
async page => {
  let signedIn = false
  let unavailable = false
  let expired = false
  const businessRequests = []
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    const user = { id: 7, username: 'assigned-analyst', role: 'admin' }
    if (path === '/api/auth/me') {
      if (unavailable) return route.fulfill({ status: 503, json: { message: '服务暂不可用' } })
      return route.fulfill({ json: signedIn ? user : { ok: false, error_code: 'AUTH_REQUIRED', message: '请先登录后再访问' } })
    }
    if (path === '/api/auth/login') {
      const body = route.request().postDataJSON()
      signedIn = body.username === user.username && body.password === 'test-password'
      return route.fulfill({ json: signedIn ? { user } : { ok: false, error_code: 'LOGIN_FAILED', message: '账号或密码错误' } })
    }
    if (path === '/api/auth/logout') {
      signedIn = false
      return route.fulfill({ json: { ok: true } })
    }
    businessRequests.push(path)
    return route.fulfill({ json: expired
      ? { ok: false, error_code: 'AUTH_REQUIRED', message: '请先登录后再访问' }
      : { reports: [], total: 0, page: 1, page_size: 20 } })
  })
  try {
    await page.goto('http://127.0.0.1:5189/?page=reports&q=腾讯')
    await page.getByRole('heading', { name: '登录 MoneyManager' }).waitFor()
    if (await page.locator('.sidebar').count() || businessRequests.length) throw new Error('Signed-out users must not mount business pages or request business data')
    if (await page.getByRole('link', { name: /注册/ }).count()) throw new Error('Registration must not be available')
    await page.screenshot({ path: '.runtime_home/login-desktop.png' })
    await page.setViewportSize({ width: 390, height: 844 })
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Login overflows mobile viewport')
    await page.screenshot({ path: '.runtime_home/login-mobile.png' })
    await page.getByLabel('账号', { exact: true }).fill('assigned-analyst')
    await page.getByLabel('密码', { exact: true }).fill('wrong-password')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await page.getByRole('alert').filter({ hasText: '账号或密码错误' }).waitFor()
    if (await page.locator('.sidebar').count()) throw new Error('Failed login must not reveal the app')
    await page.getByLabel('密码', { exact: true }).fill('test-password')
    await page.getByLabel('密码', { exact: true }).press('Enter')
    await page.locator('.sidebar').waitFor()
    if (!page.url().includes('page=reports')) throw new Error('Login lost the report deep link')
    await page.reload()
    await page.getByRole('button', { name: '退出登录', exact: true }).waitFor()
    await page.getByRole('button', { name: '退出登录', exact: true }).click()
    await page.getByRole('heading', { name: '登录 MoneyManager' }).waitFor()
    signedIn = true
    unavailable = true
    await page.reload()
    await page.getByRole('button', { name: '重试', exact: true }).waitFor()
    if (await page.locator('.sidebar').count()) throw new Error('Session check failure must not reveal the app')
    unavailable = false
    await page.getByRole('button', { name: '重试', exact: true }).click()
    await page.locator('.sidebar').waitFor()
    expired = true
    await page.getByRole('button', { name: '搜索报告', exact: true }).click()
    await page.getByRole('heading', { name: '登录 MoneyManager' }).waitFor()
    if (await page.locator('.sidebar').count()) throw new Error('Expired sessions must unmount business content')
    return 'PASS: login gate, assigned account, errors, deep link, reload, logout, expiry, service retry and mobile'
  } finally {
    await page.unroute('**/api/**')
  }
}
