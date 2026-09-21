import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

function sseEvent(id: number, payload: Record<string, unknown>) {
  return `id: ${id}\ndata: ${JSON.stringify(payload)}\n\n`
}

function completedSummary(systemId: string) {
  return {
    generated_at: '2026-08-26T12:00:00+08:00',
    system: { id: systemId, name: 'AI测试系统', department: '网络安全部', source_path: '/tmp/AI测试系统' },
    parse: { exit_code: 0, file_count: 1, recognized_count: 1, category_counts: { 定级报告: 1 }, failed_file_count: 0, failed_files: [] },
    audit: { item_count: 1, counts: { passed: 1, failed: 0, not_applicable: 0 }, items: [{
      item_id: 'DJ-02', item_name: '安全等级一致性', status: 'passed', message: '固定结论为通过',
      source_documents: ['parsed/定级报告.md'], issues: [], extracted_summary: [{ field: '声明等级', value: '第2级' }],
    }] },
  }
}

async function openRunningJob(page: Page, jobId: string, sseBody: string, auditCount = 34) {
  await page.addInitScript(id => localStorage.setItem('audit-current-job', id), jobId)
  await page.route('**/api/health', route => route.fulfill({ json: { ok: true, audit_item_count: auditCount } }))
  await page.route('**/api/systems', route => route.fulfill({ json: [] }))
  await page.route(`**/api/jobs/${jobId}/events`, route => route.fulfill({
    contentType: 'text/event-stream',
    headers: { 'Cache-Control': 'no-cache' },
    body: `retry: 10000\n${sseBody}`,
  }))
  await page.route(`**/api/jobs/${jobId}`, route => route.fulfill({ json: {
    id: jobId, system_id: 'system-ai', source_path: '/tmp/AI测试系统', status: 'running',
    stage: 'auditing', progress: 0.5, message: '正在逐项生成 AI 说明',
  }}))
  await page.goto('/')
}

test('validates one unit folder and exposes the all-audit action', async ({ page }) => {
  await page.route('**/api/systems', route => route.fulfill({ json: [] }))
  await page.route('**/api/systems/validate', route => route.fulfill({ json: {
    source_path: '/tmp/网络安全部/测试系统-2', system_name: '测试系统-2', department: '网络安全部',
    system_id: 'demo123', output_dir: '/tmp/output', recognized_count: 3,
    category_counts: { 定级报告: 1, 备案信息表: 1, 资产清单: 1 },
    expected_category_count: 10, present_category_count: 3,
    missing_categories: ['三同步报告', '符合性评测表', '符合性评测表证明材料', '风险评估报告', '应急预案', '应急演练报告', '扫描报告'],
    category_statuses: [
      { category_code: '2.1', category_name: '定级报告', count: 1, missing: false },
      { category_code: '2.2', category_name: '备案信息表', count: 1, missing: false },
      { category_code: '2.3', category_name: '资产清单', count: 1, missing: false },
      { category_code: '2.4', category_name: '三同步报告', count: 0, missing: true },
      { category_code: '2.5', category_name: '符合性评测表', count: 0, missing: true },
      { category_code: '2.6', category_name: '符合性评测表证明材料', count: 0, missing: true },
      { category_code: '2.7', category_name: '风险评估报告', count: 0, missing: true },
      { category_code: 'emergency_plan', category_name: '应急预案', count: 0, missing: true },
      { category_code: 'drill_report', category_name: '应急演练报告', count: 0, missing: true },
      { category_code: '2.8', category_name: '扫描报告', count: 0, missing: true },
    ],
    documents: [
      { name: '测试系统定级报告.docx', relative_path: '测试系统定级报告.docx', category_code: '2.1', category_name: '定级报告', size: 2048 },
      { name: '测试系统备案信息表.docx', relative_path: '测试系统备案信息表.docx', category_code: '2.2', category_name: '备案信息表', size: 1024 },
      { name: '测试系统资产清单.xlsx', relative_path: '测试系统资产清单.xlsx', category_code: '2.3', category_name: '资产清单', size: 4096 },
    ], has_previous_result: false,
  }}))
  await page.goto('/')
  await expect(page.getByText('浙江移动-定级备案报告稽核系统')).toBeVisible()
  await expect(page).toHaveTitle('浙江移动-定级备案报告稽核系统')
  await expect(page.getByText('让每一份材料')).toHaveCount(0)
  await expect(page.getByText('NEW AUDIT MISSION')).toHaveCount(0)
  await expect(page.getByText('LOCAL SECURE MODE')).toHaveCount(0)
  await page.getByPlaceholder('选择文件夹，或粘贴完整路径').fill('/tmp/网络安全部/测试系统-2')
  await page.getByRole('button', { name: '识别材料' }).click()
  await expect(page.getByText('测试系统-2')).toBeVisible()
  await expect(page.getByRole('button', { name: /开始解析并运行全部稽核/ })).toBeVisible()
  await expect(page.getByText('已识别 3/10 类 · 共 3 份文件')).toBeVisible()
  await expect(page.getByText('缺少 7 类材料')).toBeVisible()
  await expect(page.getByText('风险评估报告', { exact: true })).toBeVisible()
  await expect(page.getByText('应急预案', { exact: true })).toBeVisible()
  await expect(page.getByText('应急演练报告', { exact: true })).toBeVisible()
  await expect(page.getByText('扫描报告', { exact: true })).toBeVisible()
  await expect(page.locator('.category-chip.missing')).toHaveCount(7)
})

test('allows one configurable model for both text and image tasks', async ({ page }) => {
  await page.route('**/api/health', route => route.fulfill({ json: { ok: true, audit_item_count: 41 } }))
  await page.route('**/api/systems', route => route.fulfill({ json: [] }))
  await page.route('**/api/model-config', async route => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: {
        base_url: 'https://open.bigmodel.cn/api/paas/v4', api_key_set: false,
        api_key_masked: '', model: 'glm-5v-turbo', suggested_models: ['glm-5v-turbo', 'glm-5.3-flash'], timeout_seconds: 120, configured: false,
      } })
      return
    }
    const body = route.request().postDataJSON()
    expect(body.model).toBe('glm-5.3-flash')
    expect(body).not.toHaveProperty('text_model')
    expect(body).not.toHaveProperty('vision_model')
    await route.fulfill({ json: {
      base_url: body.base_url, api_key_set: true, api_key_masked: 'new••••1234',
      model: body.model, suggested_models: ['glm-5v-turbo', 'glm-5.3-flash'], timeout_seconds: body.timeout_seconds, configured: true,
    } })
  })
  await page.route('**/api/model-config/test', route => route.fulfill({ json: {
    ok: true, latency_ms: 128, model: 'glm-5.3-flash', message: 'OK',
  } }))

  await page.goto('/')
  await page.getByRole('button', { name: '模型配置' }).click()
  await expect(page.getByRole('heading', { name: '模型配置' })).toBeVisible()
  await expect(page.getByLabel('统一模型')).toHaveValue('glm-5v-turbo')
  await page.getByLabel('统一模型').fill('glm-5.3-flash')
  await expect(page.getByText('文本模型')).toHaveCount(0)
  await expect(page.getByText('视觉模型')).toHaveCount(0)
  await page.getByPlaceholder('填写已轮换的新密钥').fill('new-secret-1234')
  await page.getByRole('button', { name: '保存配置' }).click()
  await expect(page.getByText('配置已保存，后续稽核将使用这套统一模型配置')).toBeVisible()
  await expect(page.getByLabel('统一模型')).toHaveValue('glm-5.3-flash')
  await page.getByRole('button', { name: '测试连接' }).click()
  await expect(page.getByText('连接成功')).toBeVisible()
  await expect(page.getByText('OK')).toBeVisible()
})

test('shows extracted fields in the result column and detail drawer', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('audit-current-job', 'job-1'))
  await page.route('**/api/systems', route => route.fulfill({ json: [] }))
  await page.route('**/api/jobs/job-1', route => route.fulfill({ json: {
    id: 'job-1', system_id: 'system-1', source_path: '/tmp/测试系统-2', status: 'completed',
    stage: 'completed', progress: 1, message: '完成',
  }}))
  await page.route('**/api/systems/system-1/summary', route => route.fulfill({ json: {
    generated_at: '2026-08-24T12:00:00+08:00',
    system: { id: 'system-1', name: '测试系统-2', department: '网络安全部', source_path: '/tmp/测试系统-2' },
    parse: { exit_code: 0, file_count: 1, recognized_count: 1, category_counts: { 定级报告: 1 }, failed_file_count: 0, failed_files: [] },
    audit: { item_count: 3, counts: { passed: 1, failed: 1, not_applicable: 1 }, items: [{
      item_id: 'DJ-02', item_name: '检查定级报告的安全等级计算结果是否与声明等级一致', status: 'passed', message: '计算等级与声明等级一致', source_documents: ['parsed/定级报告.md'], issues: [],
      extracted_summary: [{ field: '社会影响力赋值', value: '2' }, { field: '声明安全等级', value: '第2级' }],
      fields_file: 'audits/DJ-02/DJ-02.json', result_file: 'audits/DJ-02/稽核结果.json',
    }, {
      item_id: 'DJ-04', item_name: '检查修订历史填写完整性', status: 'failed', reason_code: 'missing_document', message: '缺少定级报告', source_documents: [], issues: ['缺少定级报告'], extracted_summary: [],
    }, {
      item_id: 'FHX-05', item_name: '检查一级系统是否提供风险评估报告', status: 'not_applicable', reason_code: 'not_applicable', message: '非一级系统，本项不适用', source_documents: [], issues: [], extracted_summary: [],
    }] },
  }}))
  await page.route('**/api/systems/system-1/audits/DJ-02', route => route.fulfill({ json: {
    summary: {}, fields: { 社会影响力赋值: 2, 声明安全等级: 2 }, result: { passed: true },
    ai_narration: { status: 'completed', model: 'glm-5v-turbo', text: '我核对了社会影响力赋值，其值为2。因此，本项判断为通过。', cached: false },
  }}))
  await page.route('**/api/systems/system-1/audits/FHX-05', route => route.fulfill({ json: {
    summary: {}, fields: { 声明安全等级: 2 }, result: { applicable: false },
    ai_narration: { status: 'failed', model: 'glm-5v-turbo', error_code: 'timeout', error_message: '模型响应超时' },
  }}))

  await page.goto('/')
  await expect(page.getByText('中间提取内容').first()).toBeVisible()
  await expect(page.getByText('检查定级报告的安全等级计算结果是否与声明等级一致')).toBeVisible()
  await expect(page.locator('.audit-row', { hasText: 'DJ-02' }).locator('.audit-input')).toContainText('定级报告')
  await expect(page.locator('.audit-row', { hasText: 'DJ-04' }).locator('.audit-input')).toContainText('未取得输入材料')
  await expect(page.getByText(/社会影响力赋值：2/)).toBeVisible()
  await expect(page.getByText('不通过', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('不适用', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('需关注', { exact: true })).toHaveCount(0)
  const categoryFilter = page.locator('.filters select').first()
  await expect(categoryFilter).toContainText('定级报告')
  await expect(categoryFilter).toContainText('符合性评测表')
  await expect(categoryFilter).not.toContainText('DJ')
  await expect(categoryFilter).not.toContainText('FHX')
  await page.getByRole('button', { name: /DJ-02/ }).click()
  await expect(page.getByText('中间提取内容').last()).toBeVisible()
  await expect(page.locator('.extracted-field-list b', { hasText: '社会影响力赋值' })).toBeVisible()
  await expect(page.locator('.saved-ai-narration')).toContainText('我核对了社会影响力赋值')
  await page.getByRole('button', { name: '关闭详情' }).click()
  await page.getByRole('button', { name: /FHX-05/ }).click()
  await expect(page.locator('.saved-ai-failure')).toContainText('模型响应超时')
  await expect(page.locator('.detail-drawer .status-pill.not_applicable')).toHaveText('不适用')
})

test('keeps the left progress card while removing terminal rows', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.addInitScript(() => localStorage.setItem('audit-current-job', 'job-log'))
  await page.route('**/api/health', route => route.fulfill({ json: { ok: true, audit_item_count: 34 } }))
  await page.route('**/api/systems', route => route.fulfill({ json: [] }))
  await page.route('**/api/jobs/job-log/events', route => route.fulfill({
    contentType: 'text/event-stream',
    body: [
      'data: {"timestamp":"2026-08-24T14:20:15+08:00","type":"queued","message":"任务已创建"}\n\n',
      'data: {"timestamp":"2026-08-24T14:20:21+08:00","type":"log","stage":"parsing","message":"warning: The `fitz` API is deprecated and will be removed in future. Use `import pymupdf` instead."}\n\n',
    ].join(''),
  }))
  await page.route('**/api/jobs/job-log', route => route.fulfill({ json: {
    id: 'job-log', system_id: 'system-log', source_path: '/tmp/测试系统-2', status: 'running',
    stage: 'parsing', progress: 0.12, message: '正在分析系统材料',
  }}))

  await page.goto('/')
  await expect(page.getByTestId('narration-progress')).toBeVisible()
  await expect(page.locator('.progress-orbit')).toBeVisible()
  await expect(page.getByTestId('audit-stream')).toBeVisible()
  await expect(page.getByText('正在准备稽核项…')).toBeVisible()
  await expect(page.locator('.stage-list > div')).toHaveCount(3)
  await expect(page.locator('.stage-list')).toContainText('顺序稽核')
  await expect(page.locator('.stage-list')).not.toContainText('AI')
  await expect(page.locator('.stage-list')).not.toContainText('LLM')
  await expect(page.locator('.terminal, .terminal-body, .log-line, .ai-live-sections')).toHaveCount(0)
  await expect(page.getByText('实时处理日志')).toHaveCount(0)
  await expect(page.getByText(/EVENTS/)).toHaveCount(0)
  await expect(page.getByText(/14:20:21/)).toHaveCount(0)
})

test('deduplicates AI event chunks and keeps streamed text out of terminal logs', async ({ page }) => {
  const expected = '关键提取结果：等级一致👨‍👩‍👧‍👦。'
  const body = [
    sseEvent(1, { type: 'ai_started', item_id: 'DJ-02', item_name: '安全等级一致性', current: 1, total: 2, audit_status: 'passed', conclusion: '计算等级与声明等级一致', extracted_summary: [{ field: '声明等级', value: '第2级' }] }),
    sseEvent(2, { type: 'ai_delta', item_id: 'DJ-02', chunk_seq: 1, delta: '关键提取结果：' }),
    sseEvent(3, { type: 'ai_delta', item_id: 'DJ-02', chunk_seq: 1, delta: '这是重复块，不应出现。' }),
    sseEvent(4, { type: 'ai_delta', item_id: 'DJ-02', chunk_seq: 2, delta: '等级一致👨‍👩‍👧‍👦。' }),
    sseEvent(5, { type: 'ai_completed', item_id: 'DJ-02', narration: { status: 'completed', model: 'glm-5v-turbo', cached: false } }),
  ].join('')
  await openRunningJob(page, 'job-ai-dedup', body)

  const item = page.locator('.audit-stream-item[data-item-id="DJ-02"]')
  await expect(item).toBeVisible()
  await expect(item.locator('h2')).toHaveText('安全等级一致性')
  await expect(item.locator('header small')).toHaveText('DJ-02')
  await expect(item.locator('.audit-stream-copy')).toHaveText(expected)
  await expect(item.locator('.audit-stream-copy')).not.toContainText('这是重复块')
  await expect(page.getByText('计算等级与声明等级一致')).toHaveCount(0)
  await expect(page.getByText('声明等级', { exact: true })).toHaveCount(0)
  await expect(page.locator('.terminal, .terminal-body, .log-line')).toHaveCount(0)
})

test('replays a cached narration visibly instead of flashing the full text', async ({ page }) => {
  const text = '缓存说明也应该清楚地逐字显示，不能一次性闪现完整结果。'
  const body = [
    sseEvent(70, { type: 'ai_started', item_id: 'DJ-02', item_name: '缓存回放测试', current: 1, total: 1 }),
    sseEvent(71, { type: 'ai_delta', item_id: 'DJ-02', chunk_seq: 1, delta: text }),
    sseEvent(72, { type: 'ai_completed', item_id: 'DJ-02', narration: { status: 'completed', model: 'glm-5v-turbo', text, cached: true } }),
  ].join('')
  await openRunningJob(page, 'job-ai-cached-replay', body)

  const copy = page.locator('.audit-stream-item[data-item-id="DJ-02"] .audit-stream-copy')
  await page.waitForTimeout(180)
  const partial = await copy.textContent()
  expect(partial?.length).toBeGreaterThan(0)
  expect(partial?.length).toBeLessThan(text.length)
  await expect(copy).toHaveText(text, { timeout: 3000 })
})

test('buffers the next AI item until the current grapheme queue is drained', async ({ page }) => {
  const firstText = '第一项说明应该逐字完整显示，即使第二项事件已经到达，也不能提前覆盖当前内容。'
  const body = [
    sseEvent(10, { type: 'ai_started', item_id: 'DJ-01', item_name: '第一项', current: 1, total: 2, audit_status: 'passed', conclusion: '第一项固定结论' }),
    sseEvent(11, { type: 'ai_delta', item_id: 'DJ-01', chunk_seq: 1, delta: firstText }),
    sseEvent(12, { type: 'ai_completed', item_id: 'DJ-01', narration: { status: 'completed', model: 'glm-5v-turbo', text: firstText, cached: false } }),
    sseEvent(13, { type: 'ai_started', item_id: 'DJ-03', item_name: '第二项', current: 2, total: 2, audit_status: 'passed', conclusion: '第二项通过' }),
    sseEvent(14, { type: 'ai_delta', item_id: 'DJ-03', chunk_seq: 1, delta: '第二项正在生成。' }),
  ].join('')
  await openRunningJob(page, 'job-ai-queue', body, 2)

  const first = page.locator('.audit-stream-item[data-item-id="DJ-01"]')
  const second = page.locator('.audit-stream-item[data-item-id="DJ-03"]')
  await expect(first).toBeVisible()
  await expect(page.getByTestId('narration-progress-percent')).toHaveText('0')
  await page.waitForTimeout(180)
  await expect(first).toBeVisible()
  await expect(second).toBeHidden()
  await expect(page.getByTestId('narration-progress-percent')).toHaveText('0')
  await expect(first.locator('.audit-stream-copy')).toHaveText(firstText, { timeout: 4000 })
  await expect(second).toBeVisible({ timeout: 4000 })
  await expect(page.getByTestId('narration-progress-percent')).toHaveText('50')
  await expect(page.getByTestId('narration-progress-count')).toContainText('1 / 2')
  await expect(first).toBeVisible()
  await expect(first.locator('.audit-stream-copy')).toHaveText(firstText)
})

test('replays only audit completions that already contain their LLM result', async ({ page }) => {
  const firstText = '第一项规则及模型处理均已完成。'
  const secondText = '第二项规则及模型处理均已完成。'
  const body = [
    sseEvent(80, { type: 'audit_started', item_id: 'DJ-01', item_name: '第一项', current: 1, total: 2 }),
    sseEvent(81, { type: 'audit_complete', item_id: 'DJ-01', item_name: '第一项', current: 1, total: 2, status: 'passed', message: '规则通过', ai_narration: { status: 'completed', text: firstText, model: 'glm-5.3-flash', cached: false } }),
    sseEvent(82, { type: 'audit_started', item_id: 'DJ-02', item_name: '第二项', current: 2, total: 2 }),
    sseEvent(83, { type: 'audit_complete', item_id: 'DJ-02', item_name: '第二项', current: 2, total: 2, status: 'failed', message: '规则不通过', ai_narration: { status: 'completed', text: secondText, model: 'glm-5.3-flash', cached: false } }),
  ].join('')

  await openRunningJob(page, 'job-audit-first', body, 2)

  const first = page.locator('.audit-stream-item[data-item-id="DJ-01"]')
  const second = page.locator('.audit-stream-item[data-item-id="DJ-02"]')
  await expect(first.locator('.audit-stream-copy')).toHaveText(firstText, { timeout: 3000 })
  await expect(second).toBeVisible({ timeout: 3000 })
  await expect(second.locator('.audit-stream-copy')).toHaveText(secondText, { timeout: 3000 })
  await expect(first).toHaveAttribute('data-kind', 'ai')
  await expect(second).toHaveAttribute('data-kind', 'ai')
})

test('does not animate hidden partial text after an AI stream failure', async ({ page }) => {
  const partialText = '这段中途返回的文字在失败卡片中不可见，不应继续占用逐字动画时间。'.repeat(8)
  const body = [
    sseEvent(15, { type: 'ai_started', item_id: 'DJ-01', item_name: '第一项', current: 1, total: 2, audit_status: 'passed', conclusion: '第一项固定结论' }),
    sseEvent(16, { type: 'ai_delta', item_id: 'DJ-01', chunk_seq: 1, delta: partialText }),
    sseEvent(17, { type: 'ai_failed', item_id: 'DJ-01', chunk_seq: 2, error_type: 'stream_error', message: '模型流式输出中断', narration: { status: 'failed', error_code: 'stream_error', error_message: '模型流式输出中断' } }),
    sseEvent(18, { type: 'ai_started', item_id: 'DJ-03', item_name: '第二项', current: 2, total: 2, audit_status: 'passed', conclusion: '第二项固定结论' }),
    sseEvent(19, { type: 'ai_failed', item_id: 'DJ-03', chunk_seq: 1, error_type: 'timeout', message: '第二项已开始', narration: { status: 'failed', error_code: 'timeout', error_message: '第二项已开始' } }),
  ].join('')
  await openRunningJob(page, 'job-ai-partial-failure', body)

  const first = page.locator('.audit-stream-item[data-item-id="DJ-01"]')
  const second = page.locator('.audit-stream-item[data-item-id="DJ-03"]')
  await expect(second).toBeVisible({ timeout: 1600 })
  await expect(first.locator('.audit-stream-copy')).toContainText('AI说明生成失败：模型流式输出中断')
  await expect(second.locator('.audit-stream-copy')).toContainText('AI说明生成失败：第二项已开始')
  await expect(first).toBeVisible()
})

test('shows the completed AI text immediately when reduced motion is preferred', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const text = '减少动效模式下应立即显示完整文本，不执行逐字动画。'.repeat(4)
  const body = [
    sseEvent(20, { type: 'ai_started', item_id: 'FHX-05', item_name: '减少动效测试', current: 1, total: 1, audit_status: 'not_applicable', conclusion: '本项不适用' }),
    sseEvent(21, { type: 'ai_delta', item_id: 'FHX-05', chunk_seq: 1, delta: text }),
    sseEvent(22, { type: 'ai_completed', item_id: 'FHX-05', narration: { status: 'completed', model: 'glm-5v-turbo', text, cached: false } }),
  ].join('')
  await openRunningJob(page, 'job-ai-reduced', body)

  const item = page.locator('.audit-stream-item[data-item-id="FHX-05"]')
  await expect(item.locator('.audit-stream-copy')).toHaveText(text, { timeout: 750 })
  await expect(page.locator('.audit-stream-cursor')).toHaveCount(0)
})

test('shows an unconfigured AI service as a neutral skipped state', async ({ page }) => {
  const neutralMessage = 'AI未配置，已按现有逻辑完成'
  const body = [
    sseEvent(30, { type: 'ai_started', item_id: 'DJ-02', item_name: '安全等级一致性', current: 1, total: 1, chunk_seq: 0, audit_status: 'passed', conclusion: '计算等级与声明等级一致' }),
    sseEvent(31, { type: 'ai_failed', item_id: 'DJ-02', item_name: '安全等级一致性', current: 1, total: 1, chunk_seq: 1, audit_status: 'passed', conclusion: '计算等级与声明等级一致', error_type: 'not_configured', message: neutralMessage, narration: { status: 'skipped', model: 'glm-5v-turbo', text: neutralMessage, error_code: 'not_configured', cached: false } }),
  ].join('')
  await openRunningJob(page, 'job-ai-skipped', body)

  const item = page.locator('.audit-stream-item[data-item-id="DJ-02"]')
  await expect(item.locator('.audit-stream-copy')).toHaveText(neutralMessage)
  await expect(item.locator('.audit-stream-copy')).toHaveClass(/ai-stream-skipped/)
  await expect(page.locator('.log-line, .ai-failure')).toHaveCount(0)
  await expect(page.getByText('AI 说明生成失败')).toHaveCount(0)
  await expect(page.getByText('AI错误', { exact: true })).toHaveCount(0)
})

test('shows program-only reasons as normal list items without pretending they came from AI', async ({ page }) => {
  const body = [
    sseEvent(60, { type: 'audit_started', item_id: 'DJ-04', item_name: '检查修订历史填写完整性', current: 1, total: 2 }),
    sseEvent(61, { type: 'audit_complete', item_id: 'DJ-04', item_name: '检查修订历史填写完整性', current: 1, total: 2, status: 'failed', reason_code: 'missing_document', message: '缺少定级报告' }),
    sseEvent(62, { type: 'audit_started', item_id: 'DJ-05', item_name: '检查定级对象描述', current: 2, total: 2 }),
    sseEvent(63, { type: 'ai_started', item_id: 'DJ-05', item_name: '检查定级对象描述', current: 2, total: 2, chunk_seq: 0 }),
    sseEvent(64, { type: 'ai_delta', item_id: 'DJ-05', chunk_seq: 1, delta: '对象描述与材料内容一致。' }),
    sseEvent(65, { type: 'ai_completed', item_id: 'DJ-05', chunk_seq: 2, narration: { status: 'completed', model: 'glm-5v-turbo', text: '对象描述与材料内容一致。', cached: false } }),
    sseEvent(66, { type: 'audit_complete', item_id: 'DJ-05', item_name: '检查定级对象描述', current: 2, total: 2, status: 'passed', message: '通过' }),
  ].join('')
  await openRunningJob(page, 'job-program-only', body)

  const programItem = page.locator('.audit-stream-item[data-item-id="DJ-04"]')
  const aiItem = page.locator('.audit-stream-item[data-item-id="DJ-05"]')
  await expect(programItem.locator('h2')).toHaveText('检查修订历史填写完整性')
  await expect(programItem.locator('header small')).toHaveText('DJ-04')
  await expect(programItem.locator('.audit-stream-copy')).toHaveText('程序结果：缺少定级报告')
  await expect(programItem.locator('.audit-stream-copy')).not.toContainText('AI')
  await expect(aiItem).toBeVisible({ timeout: 2500 })
  await expect(aiItem.locator('.audit-stream-copy')).toHaveText('对象描述与材料内容一致。')
  await expect(programItem).toBeVisible()
})

test('waits for SSE end and the visible AI queue before opening results', async ({ page }) => {
  const jobId = 'job-ai-finish-gate'
  const systemId = 'system-ai-finish-gate'
  const text = '后端任务虽然已经完成，但当前的AI判定说明仍应逐字展示完整，然后才能进入结果页。'.repeat(2)
  let jobReads = 0
  await page.addInitScript(id => localStorage.setItem('audit-current-job', id), jobId)
  await page.route('**/api/health', route => route.fulfill({ json: { ok: true, audit_item_count: 34 } }))
  await page.route('**/api/systems', route => route.fulfill({ json: [] }))
  await page.route(`**/api/jobs/${jobId}/events`, route => route.fulfill({
    contentType: 'text/event-stream',
    body: `retry: 10000\n${[
      sseEvent(40, { type: 'ai_started', item_id: 'DJ-02', item_name: '安全等级一致性', current: 1, total: 1, chunk_seq: 0, audit_status: 'passed', conclusion: '固定结论为通过' }),
      sseEvent(41, { type: 'ai_delta', item_id: 'DJ-02', chunk_seq: 1, delta: text }),
      sseEvent(42, { type: 'ai_completed', item_id: 'DJ-02', chunk_seq: 2, narration: { status: 'completed', model: 'glm-5v-turbo', text, cached: false } }),
      'event: end\ndata: {}\n\n',
    ].join('')}`,
  }))
  await page.route(`**/api/jobs/${jobId}`, route => {
    jobReads += 1
    const completed = jobReads > 1
    return route.fulfill({ json: {
      id: jobId, system_id: systemId, source_path: '/tmp/AI测试系统', status: completed ? 'completed' : 'running',
      stage: completed ? 'completed' : 'auditing', progress: completed ? 1 : 0.9, message: completed ? '处理完成' : '正在生成AI说明',
    } })
  })
  await page.route(`**/api/systems/${systemId}/summary`, route => route.fulfill({ json: completedSummary(systemId) }))

  await page.goto('/')
  await expect(page.locator('.audit-stream-item[data-item-id="DJ-02"]')).toBeVisible()
  await page.waitForTimeout(300)
  await expect(page.locator('.results-view')).toHaveCount(0)
  await expect(page.locator('.running-view')).toBeVisible()
  await expect(page.locator('.results-view')).toBeVisible({ timeout: 7000 })
})

test('uses a bounded fallback after a completed job loses the SSE connection', async ({ page }) => {
  const jobId = 'job-ai-disconnected'
  const systemId = 'system-ai-disconnected'
  let jobReads = 0
  await page.addInitScript(id => localStorage.setItem('audit-current-job', id), jobId)
  await page.route('**/api/health', route => route.fulfill({ json: { ok: true, audit_item_count: 34 } }))
  await page.route('**/api/systems', route => route.fulfill({ json: [] }))
  await page.route(`**/api/jobs/${jobId}/events`, route => route.fulfill({
    contentType: 'text/event-stream',
    body: `retry: 10000\n${[
      sseEvent(50, { type: 'ai_started', item_id: 'DJ-02', item_name: '安全等级一致性', current: 1, total: 1, chunk_seq: 0, audit_status: 'passed', conclusion: '固定结论为通过' }),
      sseEvent(51, { type: 'ai_delta', item_id: 'DJ-02', chunk_seq: 1, delta: '已接收的说明。' }),
      sseEvent(52, { type: 'ai_completed', item_id: 'DJ-02', chunk_seq: 2, narration: { status: 'completed', model: 'glm-5v-turbo', text: '已接收的说明。', cached: false } }),
    ].join('')}`,
  }))
  await page.route(`**/api/jobs/${jobId}`, route => {
    jobReads += 1
    const completed = jobReads > 1
    return route.fulfill({ json: {
      id: jobId, system_id: systemId, source_path: '/tmp/AI测试系统', status: completed ? 'completed' : 'running',
      stage: completed ? 'completed' : 'auditing', progress: completed ? 1 : 0.9, message: completed ? '处理完成' : '正在生成AI说明',
    } })
  })
  await page.route(`**/api/systems/${systemId}/summary`, route => route.fulfill({ json: completedSummary(systemId) }))

  await page.goto('/')
  await expect(page.locator('.audit-stream-item[data-item-id="DJ-02"]')).toBeVisible()
  await page.waitForTimeout(600)
  await expect(page.locator('.results-view')).toHaveCount(0)
  await expect(page.locator('.results-view')).toBeVisible({ timeout: 5000 })
})
