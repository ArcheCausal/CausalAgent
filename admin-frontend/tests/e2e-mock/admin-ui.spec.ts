import { expect, test } from '@playwright/test'

const observedAt = '2026-07-25T12:00:01.000Z'
const mappedDigestText = 'SELECT * FROM users WHERE id = ?'
const inferredDigestText = 'SELECT id, payload FROM custom_table WHERE tenant_id = ? AND archived_at IS NULL ORDER BY created_at DESC LIMIT ?'
const overrides = {
  auto_refresh_enabled: null,
  realtime_interval_seconds: null,
  sql_interval_seconds: null,
  table_capacity_interval_seconds: null,
  slow_query_warning_delta: null,
  integrity_enabled: null,
  integrity_interval_seconds: null,
}
const effective = {
  auto_refresh_enabled: true,
  realtime_interval_seconds: 10,
  sql_interval_seconds: 60,
  table_capacity_interval_seconds: 900,
  slow_query_warning_delta: 1,
  integrity_enabled: false,
  integrity_interval_seconds: 86400,
}

test('完整看板和在线配置在 Vue 生产路由语义下可交互', async ({ page }) => {
  let version = 1
  await page.route('**/api/check_auth', route => route.fulfill({
    json: {
      isLoggedIn: true,
      username: 'mock-admin',
      role: 'admin',
      csrf_token: 'mock-csrf',
    },
  }))
  await page.route('**/api/admin/db/dashboard', route => route.fulfill({
    json: {
      success: true,
      data: {
        realtime: {
          status: 'healthy',
          observed_at: observedAt,
          source_alias: 'primary',
          primary: {
            status: 'healthy',
            value: { connected: true, version: '8.0.42' },
          },
          replica: {
            status: 'healthy',
            value: {
              configured: true,
              available: true,
              lag_seconds: 0,
              io_running: 'Yes',
              sql_running: 'Yes',
            },
          },
          connections: {
            status: 'healthy',
            value: {
              utilization_percent: 12,
              threads_connected: 12,
              max_connections: 100,
              threads_running: 2,
              max_used_connections: 20,
            },
          },
          jobs: {
            status: 'healthy',
            value: {
              summary: { queued: 1, running: 2, stale: 0, max_attempts_running: 0 },
              data: [{
                job_id: 'job-1',
                status: 'running',
                worker_id: 'worker-1',
                attempt_count: 1,
                max_attempts: 3,
                heartbeat_at: observedAt,
                created_at: observedAt,
              }],
            },
          },
        },
        capacity: {
          status: 'healthy',
          observed_at: observedAt,
          source_alias: 'replica-1',
          is_estimate: true,
          revision: {
            status: 'healthy',
            value: {
              matches: true,
              repository_heads: ['c2d3e4f5a6b7'],
              instance_revisions: ['c2d3e4f5a6b7'],
            },
          },
          tables: {
            status: 'healthy',
            is_estimate: true,
            value: [{
              table_name: 'users',
              table_rows: 10,
              data_length: 1024,
              index_length: 512,
              total_length: 1536,
            }],
          },
        },
        integrity: {
          status: 'healthy',
          observed_at: observedAt,
          source_alias: 'primary',
          blocking_count: 0,
          checks: [{
            label: '关键外键',
            value: 0,
            status: 'healthy',
            source_alias: 'primary',
          }],
        },
        sql_performance: {
          status: 'warning',
          observed_at: observedAt,
          source_alias: 'primary',
          warning: 'performance_schema digest 无权限，其他指标仍可用',
          slow_query_log: 'ON',
          long_query_time: 10,
          slow_queries_delta: 2,
          window_seconds: 60,
          slow_queries_total: 20,
          slow_query_warning_threshold: 1,
          high_load_statements: [
            {
              digest_text: mappedDigestText,
              count_star: 10,
              total_seconds: 1.2,
              avg_seconds: 0.12,
              rows_examined: 10,
              rows_sent: 10,
            },
            {
              digest: inferredDigestText,
              execution_count: 3,
              total_seconds: 0.75,
              avg_seconds: 0.25,
              rows_examined: 30,
              rows_sent: 3,
            },
          ],
        },
        refresh_policy: {
          ...effective,
          configuration_version: 1,
          configuration_state: 'current',
          configuration_warning: null,
        },
      },
    },
  }))
  await page.route('**/api/admin/db/refresh', route => {
    expect(route.request().headers()['x-csrf-token']).toBe('mock-csrf')
    return route.fulfill({
      status: 202,
      json: {
        success: true,
        data: {
          groups: ['realtime', 'sql_performance', 'capacity'],
          requested_at: '2026-07-25T12:00:00.000Z',
        },
      },
    })
  })
  await page.route('**/api/admin/db/settings/history**', route => route.fulfill({
    json: { success: true, data: { items: [], next_before_id: null } },
  }))
  await page.route('**/api/admin/db/settings', route => {
    const isWrite = route.request().method() === 'PUT'
    if (isWrite) {
      expect(route.request().headers()['x-csrf-token']).toBe('mock-csrf')
      version += 1
    }
    return route.fulfill({
      json: {
        success: true,
        data: {
          version,
          overrides,
          effective,
          sources: Object.fromEntries(Object.keys(overrides).map(key => [key, 'default'])),
          limits: {
            auto_refresh_enabled: { type: 'boolean' },
            realtime_interval_seconds: { type: 'integer', minimum: 5, maximum: 10 },
            sql_interval_seconds: { type: 'integer', minimum: 30, maximum: 60 },
            table_capacity_interval_seconds: { type: 'integer', minimum: 300, maximum: 900 },
            slow_query_warning_delta: { type: 'integer', minimum: 1, maximum: 2147483647 },
            integrity_enabled: { type: 'boolean' },
            integrity_interval_seconds: { type: 'integer', minimum: 3600, maximum: 2147483647 },
          },
          updated_by: null,
          updated_at: null,
          state: 'current',
          warning: null,
        },
      },
    })
  })

  await page.goto('/admin/database')
  await expect(page.getByRole('heading', { name: '数据库状态看板' })).toBeVisible()
  await expect(page.getByRole('link', { name: '进入 Grafana' }))
    .toHaveAttribute('href', 'http://127.0.0.1:3000/')
  await expect(page.getByRole('link', { name: '进入聊天' })).toHaveAttribute('href', '/dashboard')
  await expect(page.getByRole('link', { name: 'RAG 评测台' })).toHaveAttribute('href', '/rag-eval')
  for (const text of ['Revision', '主库', '第一从库', '阻塞项', '连接使用率']) {
    await expect(page.getByText(text, { exact: true })).toBeVisible()
  }
  await expect(page.getByText('users', { exact: true })).toBeVisible()
  await expect(page.getByText('performance_schema digest 无权限，其他指标仍可用')).toBeVisible()
  await expect(page.getByText('job-1', { exact: true })).toBeVisible()
  await expect(page.getByText(/最后采集：.*2026/)).toBeVisible()
  await expect(
    page.locator('.status-card').filter({ hasText: 'Revision' }).first().locator('.card-meta'),
  ).toHaveAttribute('title', /2026/)

  await expect(page.getByText('读取用户身份或权限', { exact: true })).toBeVisible()
  await expect(page.getByText('推断：查询 custom_table 数据', { exact: true })).toBeVisible()
  await expect(page.getByText(mappedDigestText, { exact: true })).toHaveCount(0)
  await expect(page.locator('.sql-business-table th.is-center').first()).toContainText('业务模块')
  const digestRows = page.locator('.sql-business-table .el-table__body-wrapper tbody tr')
  await expect(digestRows).toHaveCount(2)
  await expect(digestRows.nth(0)).toContainText('推断：查询 custom_table 数据')
  await expect(digestRows.nth(0)).toContainText('0.25 秒')
  await expect(digestRows.nth(1)).toContainText('读取用户身份或权限')
  await expect(digestRows.nth(1)).toContainText('0.12 秒')
  await expect(digestRows.nth(1).locator('td.is-center').first()).toContainText('用户与权限')

  await page.setViewportSize({ width: 1180, height: 900 })
  await digestRows.nth(1).getByRole('button', { name: '查看详情' }).click()
  await expect(page.getByRole('heading', { name: 'SQL详情' })).toBeVisible()
  await expect(page.locator('.sql-detail-drawer').getByText('代码确认', { exact: true })).toBeVisible()
  await expect(page.getByText('判断依据', { exact: true })).toBeVisible()
  await expect(page.getByText(/app\/auth\/service\.py/)).toBeVisible()
  await expect(page.getByText(mappedDigestText, { exact: true })).toBeVisible()
  for (const label of [
    'Digest 模板',
    '执行次数',
    '累计总耗时',
    '平均耗时',
    '扫描行',
    '返回行',
  ]) {
    await expect(page.locator('.sql-detail-drawer').getByText(label, { exact: true })).toBeVisible()
  }
  await page.getByRole('button', { name: '关闭详情' }).click()
  await expect(page.getByText(mappedDigestText, { exact: true })).toBeHidden()

  await page.setViewportSize({ width: 740, height: 900 })
  await digestRows.nth(0).getByRole('button', { name: '查看详情' }).click()
  const mobileDrawer = page.locator('.sql-detail-drawer')
  await expect(mobileDrawer).toBeVisible()
  await expect(mobileDrawer.getByText('推断', { exact: true })).toBeVisible()
  await expect(mobileDrawer.getByText(inferredDigestText, { exact: true })).toBeVisible()
  const drawerBox = await mobileDrawer.boundingBox()
  expect(drawerBox?.width).toBeGreaterThanOrEqual(739)
  expect(drawerBox?.width).toBeLessThanOrEqual(741)
  await page.getByRole('button', { name: '关闭详情' }).click()

  await page.getByRole('button', { name: '手动刷新' }).click()
  await expect(page.getByText('共享监控快照已刷新。')).toBeVisible()
  await expect(page.getByText('共享监控快照已刷新。')).toBeHidden({ timeout: 7_000 })

  await page.getByRole('button', { name: '打开后台导航' }).click()
  await page.getByRole('link', { name: '自动采集时间配置' }).click()
  await expect(page).toHaveURL(/\/admin\/database\/settings$/)
  await expect(page.getByRole('heading', { name: '采集配置' })).toBeVisible()
  await expect(page.getByText('实时状态采集周期')).toBeVisible()
  await expect(page.getByText('代码默认').first()).toBeVisible()
  for (const label of ['管理员', '错误码']) {
    const header = page.locator('thead th').filter({ hasText: label }).first()
    const headerBox = await header.boundingBox()
    expect(headerBox?.width).toBeGreaterThanOrEqual(179)
  }

  await page.getByRole('button', { name: '保存配置' }).click()
  await expect(page.getByText(/配置已保存/)).toBeVisible()
  expect(version).toBe(2)
})

test('3.2 业务页面、受控写入、敏感揭示和可收缩导航在 mock 数据下可交互', async ({ page }) => {
  const observedAt = '2026-07-26T12:00:00.000Z'
  const longSessionTitle = '用于验证悬停时展示完整内容的超长会话标题'
  let messageContentReads = 0
  let controlledWriteCalls = 0
  let fileDeleteCalls = 0

  await page.route('**/api/check_auth', route => route.fulfill({
    json: {
      isLoggedIn: true,
      username: 'readonly-admin',
      role: 'admin',
      csrf_token: 'business-csrf',
    },
  }))
  await page.route('**/api/admin/brand/logo', route => route.fulfill({
    status: 200,
    contentType: 'image/png',
    body: Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII=',
      'base64',
    ),
  }))
  await page.route('**/api/admin/jobs/workers', route => route.fulfill({
    json: {
      success: true,
      data: [],
      summary: {
        queued: 0,
        running: 0,
        waiting_input: 0,
        stale: 0,
        max_attempts_running: 0,
      },
      meta: {
        status: 'healthy',
        observed_at: observedAt,
        source_alias: 'mock-worker-snapshot',
        warning: null,
      },
      request_id: 'mock-workers',
    },
  }))
  await page.route('**/api/admin/business/**', async route => {
    const requestUrl = new URL(route.request().url())
    const path = requestUrl.pathname
    const pageData = (items: unknown[]) => ({
      success: true,
      data: { items, limit: 20, has_more: false, next_cursor: null },
      request_id: 'mock-business',
    })

    if (path.endsWith('/business/users/operations/preview')) {
      expect(route.request().headers()['x-csrf-token']).toBe('business-csrf')
      return route.fulfill({
        json: {
          success: true,
          data: {
            action: 'set_active',
            target_count: 1,
            items: [{
              id: 1,
              username: 'alice',
              current: { role: 'admin', is_active: true },
              next: { is_active: false },
              blockers: [],
            }],
            can_execute: true,
            requires_reauthentication: true,
            batch_limit: 20,
          },
        },
      })
    }
    if (path.endsWith('/business/users/operations')) {
      const body = route.request().postDataJSON()
      expect(route.request().headers()['x-csrf-token']).toBe('business-csrf')
      expect(route.request().headers()['idempotency-key']).toBeTruthy()
      if (body.reauth_password === 'wrong-admin-password') {
        return route.fulfill({
          status: 401,
          json: {
            success: false,
            code: 'reauth_failed',
            error: '当前管理员密码不正确或账号状态已变化',
            request_id: 'mock-reauth-failed',
            fields: { reauth_password: '重新认证失败' },
          },
        })
      }
      expect(body).toMatchObject({
        action: 'set_active',
        target_ids: [1],
        value: false,
        reauth_password: 'admin-current-password',
        confirmed: true,
      })
      controlledWriteCalls += 1
      return route.fulfill({
        json: {
          success: true,
          data: {
            operation_id: 'operation-1',
            operation_type: 'user_set_active',
            target_count: 1,
            replayed: false,
            items: [{
              id: 1,
              username: 'alice',
              changed: true,
              role: 'admin',
              is_active: false,
              auth_version: 2,
            }],
          },
        },
      })
    }
    if (/\/business\/files\/9\/delete-impact$/.test(path)) {
      return route.fulfill({
        json: {
          success: true,
          data: {
            file: {
              id: 9,
              user_id: 1,
              username: 'alice',
              filename: 'stored.csv',
              original_filename: 'report.csv',
              mime_type: 'text/csv',
              file_size: 24,
              upload_timestamp: observedAt,
              last_accessed_at: observedAt,
              access_count: 0,
            },
            impact: { database_rows: 1, blob_bytes: 24, owner_active_jobs: 0 },
            can_delete: true,
            blockers: [],
            requires_confirmation: 'report.csv',
            requires_reauthentication: true,
            recycle_bin: false,
          },
        },
      })
    }
    if (/\/business\/files\/9$/.test(path) && route.request().method() === 'DELETE') {
      const body = route.request().postDataJSON()
      expect(route.request().headers()['x-csrf-token']).toBe('business-csrf')
      expect(route.request().headers()['idempotency-key']).toBeTruthy()
      expect(body).toMatchObject({
        confirm_filename: 'report.csv',
        reauth_password: 'admin-current-password',
        confirmed: true,
      })
      fileDeleteCalls += 1
      return route.fulfill({
        json: {
          success: true,
          data: {
            operation_id: 'operation-2',
            operation_type: 'file_delete',
            target_count: 1,
            replayed: false,
            deleted: true,
            file_id: 9,
            filename: 'report.csv',
            blob_deleted: true,
          },
        },
      })
    }
    if (path.endsWith('/business/overview')) {
      return route.fulfill({
        json: {
          success: true,
          data: {
            metrics: [{
              key: 'users',
              label: '用户',
              value: 2,
              is_estimate: true,
              source_alias: 'primary-information-schema',
            }],
            snapshots: [{
              snapshot_key: 'realtime',
              observed_at: observedAt,
              refresh_requested_at: null,
              status: 'healthy',
              warning: null,
              source_alias: 'primary',
            }],
            observed_at: observedAt,
            source_alias: 'primary-information-schema',
            is_estimate: true,
          },
          request_id: 'mock-overview',
        },
      })
    }
    if (/\/business\/users\/1$/.test(path)) {
      return route.fulfill({
        json: {
          success: true,
          data: {
            id: 1,
            username: 'alice',
            role: 'admin',
            is_active: true,
            created_at: observedAt,
            last_login_at: observedAt,
          },
          request_id: 'mock-user-detail',
        },
      })
    }
    if (path.endsWith('/business/users')) {
      return route.fulfill({
        json: pageData([{
          id: 1,
          username: 'alice',
          role: 'admin',
          is_active: true,
          created_at: observedAt,
          last_login_at: observedAt,
        }]),
      })
    }
    if (/\/business\/sessions\/session-1\/messages$/.test(path)) {
      return route.fulfill({
        json: pageData([{
          id: 11,
          session_id: 'session-1',
          user_id: 1,
          username: 'alice',
          message_type: 'user',
          content_preview: '只读摘要',
          content_length: 10,
          has_attachment: false,
          attachment_count: 0,
          created_at: observedAt,
        }]),
      })
    }
    if (/\/business\/sessions\/session-1$/.test(path)) {
      return route.fulfill({
        json: {
          success: true,
          data: {
            id: 'session-1',
            user_id: 1,
            username: 'alice',
            title: longSessionTitle,
            created_at: observedAt,
            last_activity_at: observedAt,
            message_count: 1,
            is_archived: false,
            archived_at: null,
          },
          request_id: 'mock-session-detail',
        },
      })
    }
    if (path.endsWith('/business/sessions')) {
      return route.fulfill({
        json: pageData([{
          id: 'session-1',
          user_id: 1,
          username: 'alice',
          title: longSessionTitle,
          created_at: observedAt,
          last_activity_at: observedAt,
          message_count: 1,
          is_archived: false,
          archived_at: null,
        }]),
      })
    }
    if (/\/business\/messages\/11\/content$/.test(path)) {
      messageContentReads += 1
      return route.fulfill({
        json: {
          success: true,
          data: {
            content: '<b>只作为文本</b>',
            offset: 0,
            limit: 65536,
            total_length: 11,
            complete: true,
            next_offset: null,
          },
          request_id: 'mock-message-content',
        },
      })
    }
    if (path.endsWith('/business/jobs')) {
      return route.fulfill({
        json: pageData([{
          job_id: 'job-1',
          user_id: 1,
          username: 'alice',
          session_id: 'session-1',
          status: 'succeeded',
          worker_id: 'worker-1',
          attempt_count: 1,
          max_attempts: 3,
          has_result: true,
          locked_at: observedAt,
          heartbeat_at: observedAt,
          created_at: observedAt,
          started_at: observedAt,
          finished_at: observedAt,
          chat_saved_at: observedAt,
        }]),
      })
    }
    if (path.endsWith('/business/files')) {
      return route.fulfill({
        json: pageData([{
          id: 9,
          user_id: 1,
          username: 'alice',
          filename: 'stored.csv',
          original_filename: 'report.csv',
          mime_type: 'text/csv',
          file_size: 24,
          upload_timestamp: observedAt,
          last_accessed_at: observedAt,
          access_count: 0,
        }]),
      })
    }
    return route.fulfill({
      status: 404,
      json: {
        success: false,
        code: 'not_found',
        error: 'mock route missing',
        request_id: 'mock-missing',
      },
    })
  })
  await page.route('**/api/admin/db/audit?mode=quick', route => route.fulfill({
    json: {
      success: true,
      data: { observed_at: observedAt, checks: [] },
      request_id: 'mock-quick',
    },
  }))
  await page.route('**/api/admin/db/audit?mode=deep', route => route.fulfill({
    json: {
      success: true,
      data: {
        mode: 'deep',
        status: 'healthy',
        observed_at: observedAt,
        refresh_requested_at: null,
        refresh_pending: false,
        scheduled: false,
        source_alias: 'deep-audit-shared-snapshot',
        query_timeout_ms: 3000,
        sample_limit: 20,
        checks: [{
          key: 'revision',
          label: 'Alembic revision',
          status: 'healthy',
          summary: '迁移链一致',
          details: { matches: true },
        }],
      },
      request_id: 'mock-deep',
    },
  }))

  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/admin/overview')
  await expect(page.getByRole('heading', { name: '业务概览' })).toBeVisible()
  await expect(page.getByText('primary-information-schema')).toBeVisible()
  await expect(page.locator('.nav-item').filter({ hasText: '会话与内容管理' }))
    .toHaveCSS('font-size', '14px')
  await expect(page.locator('.nav-icon svg')).toHaveCount(8)
  await expect(page.locator('.nav-icon svg').first()).toHaveAttribute('stroke-width', '1.5')

  const grafanaEntry = page.getByRole('link', { name: '进入 Grafana' })
  const chatEntry = page.getByRole('link', { name: '进入聊天' })
  const logoutButton = page.getByRole('button', { name: '退出登录' })
  await expect(grafanaEntry).toHaveCSS('text-decoration-line', 'none')
  await expect(chatEntry).toHaveCSS('text-decoration-line', 'none')
  await expect(logoutButton).toHaveCSS('margin-left', '0px')
  const [grafanaBox, chatBox, logoutBox] = await Promise.all([
    grafanaEntry.boundingBox(),
    chatEntry.boundingBox(),
    logoutButton.boundingBox(),
  ])
  expect(grafanaBox?.x).toBe(chatBox?.x)
  expect(grafanaBox?.width).toBe(chatBox?.width)
  expect(grafanaBox?.y).toBeLessThan(chatBox?.y ?? 0)
  expect(chatBox?.x).toBe(logoutBox?.x)
  expect(chatBox?.width).toBe(logoutBox?.width)

  const logoSources = await page.locator('img[alt="CausalAgent"], .mobile-brand-icon img')
    .evaluateAll(images => images.map(image => image.getAttribute('src')))
  expect(new Set(logoSources)).toEqual(new Set(['/api/admin/brand/logo']))

  const toggle = page.getByRole('button', { name: '收起左侧导航' })
  await toggle.click()
  await expect(page.locator('.admin-shell')).toHaveClass(/sidebar-collapsed/)
  expect(await page.evaluate(() => localStorage.getItem('causalagent.admin.sidebar.collapsed')))
    .toBe('true')
  await page.reload()
  await expect(page.locator('.admin-shell')).toHaveClass(/sidebar-collapsed/)
  await page.getByRole('button', { name: '展开左侧导航' }).click()

  await page.getByRole('link', { name: '用户与权限管理' }).click()
  await expect(page.getByRole('heading', { name: '用户与权限管理' })).toBeVisible()
  await expect(page.getByText('alice', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '详情', exact: true }).click()
  await expect(page.getByText('用户详情')).toBeVisible()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '禁用', exact: true }).click()
  await expect(page.getByRole('heading', { name: '禁用用户' })).toBeVisible()
  await expect(page.getByText('禁用 / 会话失效', { exact: true })).toBeVisible()
  await page.getByLabel('当前管理员密码（重新认证）').fill('wrong-admin-password')
  await page.getByText('我已核对预览，并确认执行 禁用用户', { exact: true }).click()
  await page.getByRole('button', { name: '确认执行' }).click()
  await expect(page).toHaveURL(/\/admin\/users$/)
  await expect(page.getByRole('dialog', { name: '禁用用户' })).toBeVisible()
  await expect(page.getByText('当前管理员密码不正确，请重新输入。', { exact: false }))
    .toBeVisible()
  await expect(page.getByLabel('当前管理员密码（重新认证）')).toHaveValue('')
  await page.getByLabel('当前管理员密码（重新认证）').fill('admin-current-password')
  await page.getByRole('button', { name: '确认执行' }).click()
  await expect(page.getByText('禁用用户已完成')).toBeVisible()
  expect(controlledWriteCalls).toBe(1)

  await page.getByRole('link', { name: '会话与内容管理' }).click()
  await expect(page.getByRole('heading', { name: '会话与内容管理' })).toBeVisible()
  const sessionTitle = page.locator('.session-title-cell')
  await expect(sessionTitle).toHaveText(longSessionTitle)
  await expect(sessionTitle).toHaveCSS('text-overflow', 'ellipsis')
  await sessionTitle.hover()
  await expect(page.locator('.session-title-tooltip').getByText(longSessionTitle, { exact: true }))
    .toBeVisible()
  expect(messageContentReads).toBe(0)
  await page.getByRole('button', { name: '查看详情' }).click()
  await expect(page.getByText('只读摘要')).toBeVisible()
  const sessionDrawer = page.locator('.el-drawer').filter({ hasText: '会话详情' })
  const auditNotice = sessionDrawer.locator('.drawer-section-title + .sensitive-notice')
  await expect(auditNotice).toHaveText('读取正文将会记录管理员、目标、结果和 request ID。')
  expect(messageContentReads).toBe(0)

  await page.setViewportSize({ width: 720, height: 900 })
  await expect(auditNotice).toBeVisible()
  expect(await auditNotice.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
  await page.setViewportSize({ width: 1280, height: 900 })

  await page.getByRole('button', { name: '查看正文' }).click()
  const contentDialog = page.getByRole('dialog', { name: '消息 #11 正文' })
  await expect(contentDialog.locator('.sensitive-notice')).toHaveCount(0)
  await expect(page.getByText('<b>只作为文本</b>', { exact: true })).toBeVisible()
  expect(messageContentReads).toBe(1)
  await expect(page.locator('.sensitive-content b')).toHaveCount(0)
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')

  await page.getByRole('link', { name: '分析任务管理' }).click()
  await expect(page.getByRole('heading', { name: '分析任务管理' })).toBeVisible()
  await expect(page.getByText('job-1', { exact: true })).toBeVisible()

  await page.getByRole('link', { name: '对话文件管理' }).click()
  await expect(page.getByRole('heading', { name: '对话文件管理' })).toBeVisible()
  await expect(page.getByText('report.csv', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '删除', exact: true }).click()
  await expect(page.getByRole('heading', { name: '删除文件' })).toBeVisible()
  await expect(page.getByText('第一步：输入文件名以确认删除')).toBeVisible()
  await expect(page.getByPlaceholder('请输入完整文件名：report.csv')).toBeVisible()
  await expect(page.getByText('第二步：输入当前管理员登录密码')).toBeVisible()
  await expect(page.getByPlaceholder('请输入当前管理员密码')).toBeVisible()
  await page.getByLabel('输入文件名 report.csv 确认').fill('report.csv')
  await page.getByLabel('当前管理员密码（重新认证）').fill('admin-current-password')
  await page.getByRole('button', { name: '确认删除' }).click()
  await expect(page.getByText('文件已删除', { exact: true })).toBeVisible()
  expect(fileDeleteCalls).toBe(1)

  await page.getByRole('link', { name: 'Schema与审计' }).click()
  await expect(page.getByRole('heading', { name: 'Schema与审计' })).toBeVisible()
  await expect(page.getByText('迁移链一致')).toBeVisible()

  await page.setViewportSize({ width: 720, height: 900 })
  await page.getByRole('button', { name: '打开后台导航' }).click()
  await expect(page.locator('.admin-sidebar')).toHaveClass(/mobile-open/)
  await expect(page.getByRole('link', { name: '进入 Grafana' })).toBeVisible()
  await expect(page.getByRole('link', { name: '进入聊天' })).toBeVisible()
  await page.getByRole('button', { name: '关闭后台导航' }).first().click()
  await expect(page.locator('.admin-sidebar')).not.toHaveClass(/mobile-open/)
})
