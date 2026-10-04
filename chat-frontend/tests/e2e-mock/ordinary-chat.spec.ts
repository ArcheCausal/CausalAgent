import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

function json(payload: unknown, status = 200): { status: number; contentType: string; body: string } {
  return { status, contentType: 'application/json', body: JSON.stringify(payload) }
}

interface RecordedTraffic {
  businessPaths: string[]
}

interface MockOptions {
  loggedIn?: boolean
  sessions?: ReadonlyArray<[string, { preview: string; last_time: string }]>
  sessionMessages?: ReadonlyArray<Record<string, unknown>>
  files?: ReadonlyArray<Record<string, unknown>>
}

async function installApiMocks(page: Page, options: MockOptions = {}): Promise<RecordedTraffic> {
  const loggedIn = options.loggedIn ?? true
  const traffic: RecordedTraffic = { businessPaths: [] }

  page.on('request', (request) => {
    const pathname = new URL(request.url()).pathname
    if (!pathname.startsWith('/api/')) return
    if (pathname === '/api/check_auth') return
    traffic.businessPaths.push(pathname)
  })

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/')) {
      await route.fallback()
      return
    }
    if (url.pathname === '/api/check_auth') {
      await route.fulfill({
        ...(loggedIn
          ? json({
              isLoggedIn: true,
              username: 'alice',
              role: 'admin',
              permissions: ['dashboard.access', 'admin.access', 'rag_eval.access'],
              csrf_token: 'csrf-token',
            })
          : json({ isLoggedIn: false })),
      })
      return
    }
    if (url.pathname === '/api/sessions') {
      await route.fulfill({ ...json(options.sessions ?? []) })
      return
    }
    if (url.pathname === '/api/load_session') {
      await route.fulfill({ ...json({ success: true, messages: options.sessionMessages ?? [] }) })
      return
    }
    if (url.pathname === '/api/files') {
      await route.fulfill({ ...json(options.files ?? []) })
      return
    }
    if (url.pathname === '/api/agent/jobs/active') {
      await route.fulfill({ ...json({ success: true, jobs: [] }) })
      return
    }
    if (url.pathname === '/api/new_chat') {
      await route.fulfill({ ...json({ success: true, new_session_id: 'session-1' }) })
      return
    }
    if (url.pathname === '/api/agent/jobs' && request.method() === 'POST') {
      await route.fulfill({ ...json({ success: true, job_id: 'job-1', status: 'queued', existing: false }, 202) })
      return
    }
    if (url.pathname === '/api/agent/jobs/job-1/events') {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: [
          'id: 1\nevent: node_start\ndata: {"type":"node_start","step_id":"s1","node_name":"deep_agent","title":"执行 Deep Agent 分析"}\n\n',
          'id: 2\nevent: decision_delta\ndata: {"type":"decision_delta","step_id":"s1","node_name":"deep_agent","title":"执行 Deep Agent 分析","stream_id":"decision-1","sequence":1,"delta":"选择 PC","decision_kind":"algorithm","tool_name":"pc"}\n\n',
          'id: 3\nevent: decision\ndata: {"type":"decision","step_id":"s1","node_name":"deep_agent","title":"执行 Deep Agent 分析","summary":"选择 PC","decision_kind":"algorithm","tool_name":"pc"}\n\n',
          'id: 4\nevent: tool_call_start\ndata: {"type":"tool_call_start","step_id":"s1","node_name":"deep_agent","title":"执行 Deep Agent 分析","tool_name":"pc","argument_keys":["data"]}\n\n',
          'id: 5\nevent: tool_call_result\ndata: {"type":"tool_call_result","step_id":"s1","node_name":"deep_agent","title":"执行 Deep Agent 分析","tool_name":"pc","summary":"算法执行完成","status":"succeeded"}\n\n',
          'id: 6\nevent: text_delta\ndata: {"type":"text_delta","step_id":"s1","stream_id":"answer","sequence":1,"delta":"正在生成报告"}\n\n',
          'id: 7\nevent: node_end\ndata: {"type":"node_end","step_id":"s1","node_name":"deep_agent","title":"执行 Deep Agent 分析","duration":0.1,"status":"completed"}\n\n',
          'id: 8\nevent: final_result\ndata: {"type":"final_result","data":{"type":"text","summary":"done from mock"}}\n\n',
        ].join(''),
      })
      return
    }
    if (url.pathname === '/api/logout') {
      await route.fulfill({ ...json({ success: true }) })
      return
    }
    await route.fulfill({ ...json({ success: true }) })
  })

  return traffic
}

test('未登录访客被送到统一登录页，且不创建任何业务数据', async ({ page }) => {
  const traffic = await installApiMocks(page, { loggedIn: false })
  await page.route('**/auth/sign-in**', (route) =>
    route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>sign-in</title><h1>登录</h1>' }),
  )

  await page.goto('/')

  await expect(page).toHaveURL(/\/auth\/sign-in\?next=%2Fdashboard/)
  expect(traffic.businessPaths).toEqual([])
})

test('登录用户在工作区创建任务并看到终态事件', async ({ page }) => {
  const traffic = await installApiMocks(page)

  await page.goto('/')
  await expect(page.getByRole('button', { name: '新建对话' })).toBeVisible()

  await page.locator('textarea[placeholder="输入消息..."]').fill('请分析这个数据')
  await page.getByRole('button', { name: '发送' }).click()

  await expect(page.getByText('请分析这个数据')).toBeVisible()
  await expect(page.getByText('done from mock')).toBeVisible()
  expect(traffic.businessPaths).toContain('/api/new_chat')
  expect(traffic.businessPaths).toContain('/api/agent/jobs')
  await expect(page).toHaveURL(/dashboard-assets\/session\/session-1/)

  await page.getByRole('button', { name: /执行 Deep Agent 分析/ }).click()
  await expect(page.getByText('算法决策：选择 PC')).toBeVisible()
  await expect(page.getByText('调用工具：pc（参数字段：data）')).toBeVisible()
  await expect(page.getByText('pc：算法执行完成')).toBeVisible()
})

test('设置页由地址决定，关闭后回到工作区', async ({ page }) => {
  await installApiMocks(page)

  await page.goto('/dashboard-assets/settings')
  await expect(page.getByRole('dialog', { name: '设置' })).toBeVisible()

  await page.getByRole('button', { name: '关闭', exact: true }).click()

  await expect(page.getByRole('dialog', { name: '设置' })).toHaveCount(0)
  await expect(page).toHaveURL(/dashboard-assets\/$/)
})

test('会话详情地址会加载对应会话', async ({ page }) => {
  await installApiMocks(page, {
    sessions: [['session-9', { preview: '历史会话', last_time: '2026-09-18 09:00:00' }]],
    sessionMessages: [{ sender: 'user', text: '历史消息正文' }],
  })

  await page.goto('/dashboard-assets/session/session-9')

  await expect(page.getByText('历史消息正文')).toBeVisible()
  await expect(page.locator('.session-item.selected')).toContainText('历史会话')
})

test('文件列表超过三个 CSV 时每个文件卡片保持完整高度并可滚动', async ({ page }) => {
  await installApiMocks(page, {
    files: Array.from({ length: 4 }, (_, index) => ({
      id: index + 1,
      user_file_id: index + 1,
      filename: `report-${index + 1}.csv`,
      mime_type: 'text/csv',
      file_size: 128,
      uploaded_at: `2026-09-${String(21 - index).padStart(2, '0')}T09:00:00Z`,
      last_accessed_at: null,
      access_count: 0,
    })),
  })

  await page.setViewportSize({ width: 449, height: 382 })
  await page.goto('/')
  await page.getByRole('button', { name: '打开菜单' }).click()

  const fileItems = page.locator('.file-item')
  await expect(fileItems).toHaveCount(4)
  await expect(fileItems.nth(3)).toContainText('report-4.csv')

  const heights = await fileItems.evaluateAll((items) => items.map((item) => item.getBoundingClientRect().height))
  expect(heights.every((height) => height >= 70)).toBe(true)
  const filesSection = page.locator('.files-section')
  await expect(filesSection).toHaveCSS('overflow-y', 'auto')
  const scrollMetrics = await filesSection.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }))
  expect(scrollMetrics.scrollHeight).toBeGreaterThan(scrollMetrics.clientHeight)
})

test('退出登录后回到官网首页', async ({ page }) => {
  const traffic = await installApiMocks(page)

  await page.setViewportSize({ width: 1600, height: 1000 })
  await page.goto('/')
  await page.getByRole('button', { name: '用户信息' }).click()
  const homeNavigation = page.waitForRequest((request) => new URL(request.url()).pathname === '/')
  await page.getByRole('button', { name: '退出登录' }).click()

  await homeNavigation
  expect(traffic.businessPaths).toContain('/api/logout')
})
