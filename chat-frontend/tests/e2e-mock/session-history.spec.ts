import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

/* 重新加载会话后的展示事实：执行记录只出现一次，报告正文没有独立底色。 */

function json(payload: unknown, status = 200): { status: number; contentType: string; body: string } {
  return { status, contentType: 'application/json', body: JSON.stringify(payload) }
}

const reportDocument = {
  schema_version: 1,
  report_id: 'report_1',
  title: '因果分析报告',
  blocks: [
    {
      id: 'section_1',
      type: 'section',
      title: '结论摘要',
      children: [
        { id: 'markdown_1', type: 'markdown', content: '## 结论\n\n- 变量 X 与变量 Y 呈正相关' },
      ],
    },
  ],
  assets: {},
  sources: [],
  evidence_refs: [],
}

/* 与 /api/load_session 的真实返回同构：提问消息带历史阶段，答案消息只带任务关联。 */
const historyMessages = [
  {
    sender: 'user',
    text: '请分析这份数据',
    analysis_job_id: 'job-1',
    analysis_job_input_id: 21,
    thinking_after: {
      phase_sequence: 0,
      status: 'completed',
      elapsed_seconds: 12.4,
      last_event_id: 8,
      events: [
        { type: 'node_start', event_id: 1, step_id: 's1', node_name: 'deep_agent', title: '执行 Deep Agent 分析' },
        { type: 'tool_call_start', event_id: 2, step_id: 's1', node_name: 'deep_agent', title: '执行 Deep Agent 分析', tool_name: 'causal_pc', argument_keys: ['data'] },
        { type: 'tool_call_result', event_id: 3, step_id: 's1', node_name: 'deep_agent', title: '执行 Deep Agent 分析', tool_name: 'causal_pc', summary: '算法执行完成', status: 'succeeded' },
        { type: 'node_end', event_id: 4, step_id: 's1', node_name: 'deep_agent', title: '执行 Deep Agent 分析', duration: 4, status: 'completed' },
      ],
      analysis_job_id: 'job-1',
      analysis_job_input_id: 21,
    },
  },
  {
    sender: 'ai',
    text: { type: 'report', layout: 'report', render_mode: 'structured', document: reportDocument },
    analysis_job_id: 'job-1',
    analysis_job_input_id: 21,
  },
]

interface MockOptions {
  sessionMessages?: ReadonlyArray<Record<string, unknown>>
  activeJobs?: ReadonlyArray<Record<string, unknown>>
  eventStream?: string
}

async function installApiMocks(page: Page, options: MockOptions = {}): Promise<void> {
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) {
      await route.fallback()
      return
    }
    if (url.pathname === '/api/check_auth') {
      await route.fulfill({ ...json({ isLoggedIn: true, username: 'alice', role: 'admin', permissions: ['dashboard.access'], csrf_token: 'csrf-token' }) })
      return
    }
    if (url.pathname === '/api/sessions') {
      await route.fulfill({ ...json([['session-1', { preview: 'Sachs 分析', last_time: '2026-09-20 08:04:35' }]]) })
      return
    }
    if (url.pathname === '/api/load_session') {
      await route.fulfill({ ...json({ success: true, messages: options.sessionMessages ?? historyMessages }) })
      return
    }
    if (url.pathname === '/api/agent/jobs/active') {
      await route.fulfill({ ...json({ success: true, jobs: options.activeJobs ?? [] }) })
      return
    }
    if (url.pathname === '/api/agent/jobs/job-1/events') {
      await route.fulfill({ status: 200, contentType: 'text/event-stream', body: options.eventStream ?? '' })
      return
    }
    await route.fulfill({ ...json([]) })
  })
}

test('重新加载会话后任务执行记录只出现一次', async ({ page }) => {
  await installApiMocks(page)
  await page.goto('/dashboard-assets/session/session-1')

  await expect(page.getByText('请分析这份数据')).toBeVisible()
  await expect(page.locator('.report-title')).toHaveText('因果分析报告')
  await expect(page.locator('.thinking-block')).toHaveCount(1)
  await expect(page.locator('.thinking-header')).toContainText('已处理')
  await expect(page.getByText('算法执行完成', { exact: false })).toHaveCount(1)
})

test('报告正文没有浅灰色底色和边框', async ({ page }) => {
  await installApiMocks(page)
  await page.goto('/dashboard-assets/session/session-1')

  const report = page.locator('.report-document')
  await expect(report).toBeVisible()
  await expect(report).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)')
  await expect(report).toHaveCSS('border-top-width', '0px')
})

test('刷新时仍在执行的会话从历史阶段继续接收答案', async ({ page }) => {
  const runningPhase = {
    ...historyMessages[0]?.thinking_after,
    status: 'running',
    events: [{ type: 'node_start', event_id: 1, step_id: 's1', node_name: 'deep_agent', title: '执行 Deep Agent 分析' }],
  }
  await installApiMocks(page, {
    /* 运行中的会话在刷新时还没有答案消息，提问消息自带进行中的阶段。 */
    sessionMessages: [{ ...historyMessages[0], thinking_after: runningPhase }],
    activeJobs: [{ job_id: 'job-1', session_id: 'session-1', status: 'running', last_event_id: 8 }],
    eventStream: [
      'id: 9\nevent: node_start\ndata: {"type":"node_start","step_id":"s2","node_name":"deep_agent","title":"继续分析"}\n\n',
      'id: 10\nevent: final_result\ndata: {"type":"final_result","data":{"type":"text","summary":"刷新后完成的答案"}}\n\n',
    ].join(''),
  })

  await page.goto('/dashboard-assets/session/session-1')

  await expect(page.locator('.thinking-block')).toHaveCount(1)
  await expect(page.getByText('继续分析')).toBeVisible()
  await expect(page.getByText('刷新后完成的答案')).toBeVisible()
})
