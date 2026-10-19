import { expect, test } from '@playwright/test'

test('login gate, CSRF upload, logout and account switch clear private state', async ({ page }) => {
  let loggedIn = false
  let username = 'alice'
  let sawCsrf = false
  await page.route('**/api/auth/me', route => loggedIn
    ? route.fulfill({ json: { user: { id: username, username, role: 'user' }, csrf_token: 'csrf-test' } })
    : route.fulfill({ status: 401, json: { detail: '请先登录' } }))
  await page.route('**/api/auth/login', async route => {
    const body = route.request().postDataJSON()
    if (body.password !== 'valid-password') return route.fulfill({ status: 401, json: { detail: '账号或密码错误' } })
    loggedIn = true
    username = body.username
    return route.fulfill({ json: { user: { id: username, username, role: 'user' }, csrf_token: 'csrf-test' } })
  })
  await page.route('**/api/auth/logout', route => {
    expect(route.request().headers()['x-csrf-token']).toBe('csrf-test')
    loggedIn = false
    return route.fulfill({ json: { ok: true } })
  })
  await page.route('**/api/health', route => route.fulfill({ json: { ok: true, audit_item_count: 54 } }))
  await page.route('**/api/systems', route => route.fulfill({ json: loggedIn && username === 'alice'
    ? [{ id: 'alice-system', display_name: 'Alice专属系统', department: '测试', updated_at: '2026-10-19' }] : [] }))
  await page.route('**/api/systems/validate', route => {
    sawCsrf = route.request().headers()['x-csrf-token'] === 'csrf-test'
    return route.fulfill({ status: 422, json: { detail: '测试材料路径' } })
  })
  await page.goto('/')
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible()
  await expect(page.getByText('Alice专属系统')).toHaveCount(0)
  await page.getByLabel('账号', { exact: true }).fill('alice')
  await page.getByLabel('密码', { exact: true }).fill('wrong')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('alert')).toHaveText('账号或密码错误')
  await page.getByLabel('密码', { exact: true }).fill('valid-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByText('Alice专属系统')).toBeVisible()
  await expect(page.getByRole('button', { name: '模型配置' })).toHaveCount(0)
  await page.getByPlaceholder('选择文件夹，或粘贴完整路径').fill('/alice/material')
  await page.getByRole('button', { name: '识别材料' }).click()
  await expect(page.getByText('测试材料路径')).toBeVisible()
  expect(sawCsrf).toBe(true)
  await page.getByRole('button', { name: '退出登录' }).click()
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible()
  await expect(page.getByText('Alice专属系统')).toHaveCount(0)
  await page.getByLabel('账号', { exact: true }).fill('bob')
  await page.getByLabel('密码', { exact: true }).fill('valid-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('button', { name: '退出登录' })).toBeVisible()
  await expect(page.getByText('Alice专属系统')).toHaveCount(0)
  await expect(page.getByPlaceholder('选择文件夹，或粘贴完整路径')).toHaveValue('')
})

test('401 during use returns to login and clears the dashboard', async ({ page }) => {
  await page.route('**/api/auth/me', route => route.fulfill({ json: {
    user: { id: 'alice', username: 'alice', role: 'user' }, csrf_token: 'csrf',
  } }))
  await page.route('**/api/health', route => route.fulfill({ json: { ok: true, audit_item_count: 54 } }))
  await page.route('**/api/systems', route => route.fulfill({ status: 401, json: { detail: '登录过期' } }))
  await page.goto('/')
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '退出登录' })).toHaveCount(0)
})

test('login page renders on desktop and mobile', async ({ page }) => {
  await page.route('**/api/auth/me', route => route.fulfill({ status: 401, json: { detail: '请先登录' } }))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '定级备案报告稽核系统' })).toBeVisible()
  await page.screenshot({ path: 'test-results/login-desktop.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: 'test-results/login-mobile.png', fullPage: true })
})
