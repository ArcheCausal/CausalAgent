import { expect, test } from '@playwright/test'

const username = process.env.PLAYWRIGHT_ADMIN_USERNAME
const password = process.env.PLAYWRIGHT_ADMIN_PASSWORD
const normalUsername = process.env.PLAYWRIGHT_USER_USERNAME
const normalPassword = process.env.PLAYWRIGHT_USER_PASSWORD

test.describe('管理员 Vue 与真实共享快照', () => {
  test.skip(
    !username || !password,
    '仅在隔离环境提供 PLAYWRIGHT_ADMIN_USERNAME/PASSWORD 后执行真实管理员写流程',
  )

  test('管理员直接访问、登录、看板刷新、审计与配置版本流程', async ({ page, context }) => {
    await page.goto('/admin/database')
    await expect(page).toHaveURL(/\/auth\/sign-in\?next=%2Fadmin%2Fdatabase$/)

    await page.locator('#loginUsername').fill(username!)
    await page.locator('#loginPassword').fill(password!)
    await page.getByRole('button', { name: /登录/ }).click()
    await expect(page).toHaveURL(/\/admin\/database$/)
    await expect(page.getByRole('heading', { name: '数据库状态看板' })).toBeVisible()
    await expect(page.getByText('Revision', { exact: true })).toBeVisible()
    await expect(page.getByText('Worker / Job 快照')).toBeVisible()

    await page.getByRole('link', { name: '进入聊天' }).click()
    await expect(page).toHaveURL(/\/$/)
    await expect(page.locator('#mainContainer')).toBeVisible()
    await page.locator('#userAvatar').evaluate(element => (element as HTMLElement).click())
    await expect(page.getByRole('button', { name: '管理后台' })).toBeVisible()
    await page.getByRole('button', { name: '管理后台' }).click()
    await expect(page).toHaveURL(/\/admin\/database$/)

    await page.getByRole('button', { name: '手动刷新' }).click()
    await expect(page.getByText('共享监控快照已刷新。')).toBeVisible({ timeout: 65_000 })

    await page.getByRole('button', { name: '执行完整性审计' }).click()
    await expect(page.getByText('完整性审计快照已更新。')).toBeVisible({ timeout: 65_000 })

    await page.getByRole('link', { name: '自动采集时间配置' }).click()
    await expect(page.getByRole('heading', { name: '采集配置' })).toBeVisible()
    await expect(page.getByText('数据库覆盖').first()).toBeVisible()
    await expect(page.getByRole('heading', { name: '配置变更记录' })).toBeVisible()

    const stalePage = await context.newPage()
    await stalePage.goto('/admin/database/settings')
    await expect(stalePage.getByRole('heading', { name: '采集配置' })).toBeVisible()

    await page.getByRole('button', { name: '保存配置' }).click()
    await expect(page.getByText(/配置已保存/)).toBeVisible()

    await stalePage.getByRole('button', { name: '保存配置' }).click()
    await expect(stalePage.getByText('版本冲突')).toBeVisible()
    await stalePage.getByRole('button', { name: '重新加载' }).click()

    await stalePage.getByRole('button', { name: '重置全部' }).click()
    await stalePage.getByRole('button', { name: '确认重置' }).click()
    await expect(stalePage.getByText(/全部数据库覆盖值已重置/)).toBeVisible()
  })

  test('普通用户仍进入原聊天页面', async ({ page }) => {
    test.skip(!normalUsername || !normalPassword, '未提供隔离环境普通用户凭据')
    await page.goto('/')
    await page.locator('#loginUsername').fill(normalUsername!)
    await page.locator('#loginPassword').fill(normalPassword!)
    await page.getByRole('button', { name: /登录/ }).click()

    await expect(page).not.toHaveURL(/\/admin\//)
    await expect(page.locator('#mainContainer')).toBeVisible()
    await page.locator('#userAvatar').evaluate(element => (element as HTMLElement).click())
    await expect(page.getByRole('button', { name: '管理后台' })).toBeHidden()

    const forbiddenResponse = await page.request.get('/admin/database')
    expect(forbiddenResponse.status()).toBe(403)
    expect(await forbiddenResponse.text()).toContain('无管理员权限')

    const dialogPromise = page.waitForEvent('dialog')
    await page.goto('/admin/database')
    const dialog = await dialogPromise
    expect(dialog.message()).toBe('无管理员权限')
    await dialog.accept()
    await expect(page).toHaveURL(/\/$/)
    await expect(page.locator('#mainContainer')).toBeVisible()
  })

  test('管理员按已知主键核对 3.1 页面、延迟正文、文件副作用与 deep 审计', async ({ page }) => {
    await page.goto('/')
    await page.locator('#loginUsername').fill(username!)
    await page.locator('#loginPassword').fill(password!)
    await page.getByRole('button', { name: /登录/ }).click()
    await expect(page).toHaveURL(/\/admin\/database$/)

    await page.getByRole('link', { name: '业务概览' }).click()
    await expect(page.getByRole('heading', { name: '业务概览' })).toBeVisible()
    await expect(page.locator('img[alt="CausalAgent"]')).toHaveAttribute(
      'src',
      '/api/admin/brand/logo',
    )

    await page.getByRole('link', { name: '用户与权限管理' }).click()
    await expect(page.getByText('e2e-admin-31', { exact: true })).toBeVisible()
    await expect(page.getByText('e2e-user-31', { exact: true })).toBeVisible()

    await page.getByRole('link', { name: '会话与内容管理' }).click()
    await expect(page.getByText('31-user-session', { exact: true })).toBeVisible()
    await page.locator('tr', { hasText: '31-user-session' })
      .getByRole('button', { name: '查看详情' })
      .click()
    await expect(page.getByText('E2E_MESSAGE_BODY_MARKER_31')).toHaveCount(0)
    await page.locator('tr', { hasText: '3101' })
      .getByRole('button', { name: '查看正文' })
      .click()
    await expect(page.getByText('E2E_MESSAGE_BODY_MARKER_31', { exact: true })).toBeVisible()
    await page.keyboard.press('Escape')
    const aiMessageRow = page.locator('tr', { hasText: '3102' })
    await aiMessageRow.getByRole('button', { name: '查看附件' }).click()
    await expect(page.getByText('causal_graph', { exact: true })).toBeVisible()
    await page.locator('tr', { hasText: 'causal_graph' })
      .getByRole('button', { name: '查看正文' })
      .click()
    await expect(page.getByText(/E2E_ATTACHMENT_RESULT_MARKER_31/)).toBeVisible()
    await page.keyboard.press('Escape')
    await page.keyboard.press('Escape')
    await page.keyboard.press('Escape')

    await page.getByRole('link', { name: '分析任务管理' }).click()
    await expect(page.getByText('31-job-succeeded', { exact: true })).toBeVisible()
    await expect(page.getByText('E2E_JOB_RESULT_MARKER_31')).toHaveCount(0)
    await page.locator('tr', { hasText: '31-job-succeeded' })
      .getByRole('button', { name: '查看详情' })
      .click()
    await page.getByRole('button', { name: '查看结果' }).click()
    await expect(page.getByText(/E2E_JOB_RESULT_MARKER_31/)).toBeVisible()
    await page.keyboard.press('Escape')
    await page.keyboard.press('Escape')

    await page.getByRole('link', { name: '对话文件管理' }).click()
    const fileRow = page.locator('tr', { hasText: '31-report.csv' })
    await expect(fileRow).toBeVisible()
    await fileRow.getByRole('button', { name: '安全预览' }).click()
    await expect(page.getByText('<script>alert(1)</script>', { exact: true })).toBeVisible()
    await expect(page.locator('.csv-preview-table script')).toHaveCount(0)
    await page.keyboard.press('Escape')
    const downloadPromise = page.waitForEvent('download')
    await fileRow.getByRole('button', { name: '下载' }).click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toBe('31-report.csv')

    await page.getByRole('link', { name: 'Schema与审计' }).click()
    await page.getByRole('button', { name: '运行 deep 审计' }).click()
    await expect(page.getByText('deep 审计已由独立 monitor 完成。')).toBeVisible({
      timeout: 70_000,
    })
    await expect(page.getByText('Alembic revision')).toBeVisible()
  })

  test('3.2 用户、文件、幂等与最后管理员保护形成真实写入闭环', async ({ page }) => {
    await page.goto('/')
    await page.locator('#loginUsername').fill(username!)
    await page.locator('#loginPassword').fill(password!)
    await page.getByRole('button', { name: /登录/ }).click()
    await expect(page).toHaveURL(/\/admin\/database$/)

    await page.getByRole('link', { name: '用户与权限管理' }).click()
    const search = page.getByPlaceholder('按用户名开头搜索')

    await search.fill('e2e-control-a-32')
    await page.getByRole('button', { name: '筛选' }).click()
    let controlRow = page.locator('tr', { hasText: 'e2e-control-a-32' })
    await controlRow.getByRole('button', { name: '升为管理员' }).click()
    let operationDialog = page.getByRole('dialog')
    await expect(operationDialog.getByText('user / 启用', { exact: true })).toBeVisible()
    await expect(operationDialog.getByText('admin / 会话失效', { exact: true })).toBeVisible()
    await operationDialog.getByLabel('当前管理员密码（重新认证）').fill(password!)
    await operationDialog.getByRole('checkbox', { name: /我已核对预览/ }).check()
    await operationDialog.getByRole('button', { name: '确认执行' }).click()
    await expect(page.getByText('设为管理员已完成')).toBeVisible()
    controlRow = page.locator('tr', { hasText: 'e2e-control-a-32' })
    await expect(controlRow.getByText('管理员', { exact: true })).toBeVisible()

    await controlRow.getByRole('button', { name: '禁用' }).click()
    operationDialog = page.getByRole('dialog')
    await operationDialog.getByLabel('当前管理员密码（重新认证）').fill(password!)
    await operationDialog.getByRole('checkbox', { name: /我已核对预览/ }).check()
    await operationDialog.getByRole('button', { name: '确认执行' }).click()
    await expect(page.getByText('禁用用户已完成')).toBeVisible()
    controlRow = page.locator('tr', { hasText: 'e2e-control-a-32' })
    await expect(controlRow.getByText('已禁用', { exact: true })).toBeVisible()

    await controlRow.getByRole('button', { name: '启用' }).click()
    operationDialog = page.getByRole('dialog')
    await operationDialog.getByLabel('当前管理员密码（重新认证）').fill(password!)
    await operationDialog.getByRole('checkbox', { name: /我已核对预览/ }).check()
    await operationDialog.getByRole('button', { name: '确认执行' }).click()
    await expect(page.getByText('启用用户已完成')).toBeVisible()

    await search.fill('e2e-control-')
    await page.getByRole('button', { name: '筛选' }).click()
    const controlARow = page.locator('tr', { hasText: 'e2e-control-a-32' })
    const controlBRow = page.locator('tr', { hasText: 'e2e-control-b-32' })
    await controlARow.locator('.el-checkbox').click()
    await controlBRow.locator('.el-checkbox').click()
    await page.getByRole('button', { name: '设置同一新密码' }).click()
    operationDialog = page.getByRole('dialog')
    await expect(operationDialog.getByText('密码更新、会话失效')).toHaveCount(2)
    await operationDialog.getByRole('textbox', { name: '同一新密码', exact: true })
      .fill('Batch-control-password-32')
    await operationDialog.getByLabel('当前管理员密码（重新认证）').fill(password!)
    await operationDialog.getByRole('checkbox', { name: /我已核对预览/ }).check()
    await operationDialog.getByRole('button', { name: '确认执行' }).click()
    await expect(page.getByText('设置同一新密码已完成')).toBeVisible()

    const identityResponse = await page.request.get('/api/check_auth')
    const identity = await identityResponse.json()
    const idempotencyHeaders = {
      'X-CSRF-Token': identity.csrf_token,
      'Idempotency-Key': 'e2e-idempotency-replay-32',
    }
    const idempotentBody = {
      action: 'set_active',
      target_ids: [3104],
      value: true,
      reauth_password: password,
      confirmed: true,
    }
    const firstAttempt = await page.request.post('/api/admin/business/users/operations', {
      headers: idempotencyHeaders,
      data: idempotentBody,
    })
    expect(firstAttempt.status()).toBe(200)
    expect((await firstAttempt.json()).data.replayed).toBe(false)
    const replayAttempt = await page.request.post('/api/admin/business/users/operations', {
      headers: idempotencyHeaders,
      data: idempotentBody,
    })
    expect(replayAttempt.status()).toBe(200)
    expect((await replayAttempt.json()).data.replayed).toBe(true)
    const conflictAttempt = await page.request.post('/api/admin/business/users/operations', {
      headers: idempotencyHeaders,
      data: { ...idempotentBody, value: false },
    })
    expect(conflictAttempt.status()).toBe(409)
    expect((await conflictAttempt.json()).code).toBe('idempotency_conflict')

    await search.fill('e2e-control-a-32')
    await page.getByRole('button', { name: '筛选' }).click()
    controlRow = page.locator('tr', { hasText: 'e2e-control-a-32' })
    await controlRow.getByRole('button', { name: '降为用户' }).click()
    operationDialog = page.getByRole('dialog')
    await operationDialog.getByLabel('当前管理员密码（重新认证）').fill(password!)
    await operationDialog.getByRole('checkbox', { name: /我已核对预览/ }).check()
    await operationDialog.getByRole('button', { name: '确认执行' }).click()
    await expect(page.getByText('设为普通用户已完成')).toBeVisible()

    await search.fill('e2e-admin-31')
    await page.getByRole('button', { name: '筛选' }).click()
    const actorRow = page.locator('tr', { hasText: 'e2e-admin-31' })
    await actorRow.getByRole('button', { name: '禁用' }).click()
    operationDialog = page.getByRole('dialog')
    await expect(operationDialog.getByText('不能禁用当前操作者')).toBeVisible()
    await expect(operationDialog.getByText('操作会移除最后一个启用管理员')).toBeVisible()
    await expect(operationDialog.getByRole('button', { name: '确认执行' })).toBeDisabled()
    await page.keyboard.press('Escape')

    await search.fill('e2e-delete-32')
    await page.getByRole('button', { name: '筛选' }).click()
    const deleteUserRow = page.locator('tr', { hasText: 'e2e-delete-32' })
    await deleteUserRow.getByRole('button', { name: '删除' }).click()
    const userDeleteDialog = page.getByRole('dialog')
    await expect(userDeleteDialog.getByText('checkpoints', { exact: true })).toBeVisible()
    await userDeleteDialog.getByLabel('输入用户名 e2e-delete-32 确认').fill('e2e-delete-32')
    await userDeleteDialog.getByLabel('当前管理员密码（重新认证）').fill(password!)
    await userDeleteDialog.getByRole('button', { name: '确认物理删除' }).click()
    await expect(page.getByText('用户已物理删除')).toBeVisible()
    await expect(page.getByText('e2e-delete-32', { exact: true })).toHaveCount(0)

    await page.getByRole('link', { name: '对话文件管理' }).click()
    await page.getByPlaceholder('按原始文件名开头搜索').fill('32-delete-file')
    await page.getByRole('button', { name: '筛选' }).click()
    const deleteFileRow = page.locator('tr', { hasText: '32-delete-file.csv' })
    await deleteFileRow.getByRole('button', { name: '删除', exact: true }).click()
    const fileDeleteDialog = page.getByRole('dialog')
    await fileDeleteDialog.getByLabel('输入文件名 32-delete-file.csv 确认')
      .fill('32-delete-file.csv')
    await fileDeleteDialog.getByLabel('当前管理员密码（重新认证）').fill(password!)
    await fileDeleteDialog.getByRole('button', { name: '确认删除' }).click()
    await expect(page.getByText('文件已删除', { exact: true })).toBeVisible()
    await expect(page.getByText('32-delete-file.csv', { exact: true })).toHaveCount(0)
  })

  test('普通用户对全部 3.2 管理页面/API 为 403，原 SSE 与附件恢复不变', async ({
    page,
  }) => {
    test.skip(!normalUsername || !normalPassword, '未提供隔离环境普通用户凭据')
    await page.goto('/')
    await page.locator('#loginUsername').fill(normalUsername!)
    await page.locator('#loginPassword').fill(normalPassword!)
    await page.getByRole('button', { name: /登录/ }).click()
    await expect(page.locator('#mainContainer')).toBeVisible()

    for (const path of [
      '/admin/overview',
      '/admin/users',
      '/admin/sessions',
      '/admin/jobs',
      '/admin/files',
      '/admin/database/audit',
    ]) {
      const response = await page.request.get(path)
      expect(response.status(), path).toBe(403)
    }
    for (const path of [
      '/api/admin/business/overview',
      '/api/admin/business/users',
      '/api/admin/business/sessions',
      '/api/admin/business/jobs',
      '/api/admin/business/files',
      '/api/admin/db/audit?mode=deep',
      '/api/admin/brand/logo',
    ]) {
      const response = await page.request.get(path)
      expect(response.status(), path).toBe(403)
    }
    for (const request of [
      page.request.post('/api/admin/business/users/operations/preview', {
        data: { action: 'set_active', target_ids: [3102], value: false },
      }),
      page.request.post('/api/admin/business/users/operations', {
        headers: { 'Idempotency-Key': 'ordinary-user-denied-32' },
        data: {
          action: 'set_active',
          target_ids: [3102],
          value: false,
          reauth_password: normalPassword,
          confirmed: true,
        },
      }),
      page.request.delete('/api/admin/business/users/3102', {
        headers: { 'Idempotency-Key': 'ordinary-delete-denied-32' },
        data: {
          confirm_username: 'e2e-user-31',
          reauth_password: normalPassword,
          confirmed: true,
        },
      }),
      page.request.delete('/api/admin/business/files/3101', {
        headers: { 'Idempotency-Key': 'ordinary-file-denied-32' },
        data: {
          confirm_filename: '31-report.csv',
          reauth_password: normalPassword,
          confirmed: true,
        },
      }),
    ]) {
      const response = await request
      expect(response.status()).toBe(403)
    }

    const restored = await page.request.get('/api/load_session?session=31-user-session')
    expect(restored.status()).toBe(200)
    const restoredBody = await restored.json()
    expect(JSON.stringify(restoredBody)).toContain('E2E_ATTACHMENT_RESULT_MARKER_31')

    const sse = await page.request.get('/api/agent/jobs/31-job-succeeded/events')
    expect(sse.status()).toBe(200)
    const stream = await sse.text()
    expect(stream).toContain('event: final_result')
    expect(stream).toContain('E2E_JOB_RESULT_MARKER_31')
  })
})
