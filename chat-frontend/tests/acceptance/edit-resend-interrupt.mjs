/* Historical fold interrupt acceptance with the real Flask, Worker, model and MCP. */
import assert from 'node:assert/strict'
import console from 'node:console'
import { randomUUID } from 'node:crypto'
import process from 'node:process'
import { setTimeout } from 'node:timers/promises'
import { fileURLToPath, URL } from 'node:url'
import { chromium } from '@playwright/test'

const baseURL = process.env.CHAT_EDIT_TEST_URL
if (!baseURL) throw new Error('Set CHAT_EDIT_TEST_URL to the isolated deployment.')
const browser = await chromium.launch({ channel: process.env.CHAT_EDIT_BROWSER_CHANNEL })
const context = await browser.newContext({ baseURL })

async function data(response) {
  const body = await response.json()
  assert.ok(response.ok(), `HTTP ${response.status()} ${body.code ?? ''}`)
  return body
}

async function waitFor(check, description, timeout = 240_000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const result = await check()
    if (result) return result
    await setTimeout(700)
  }
  throw new Error(description)
}

try {
  const username = `fork_${randomUUID().slice(0, 12)}`
  const password = `A9${randomUUID()}`
  const credentials = { username, password }
  await data(await context.request.post('/api/register', { data: credentials }))
  await data(await context.request.post('/api/login', { data: credentials }))
  const { new_session_id: session } = await data(await context.request.post('/api/new_chat'))
  const page = await context.newPage()
  await page.goto(`/dashboard/session/${session}`)

  const load = async () => data(await context.request.get(`/api/load_session?session=${session}`))
  const dataset = fileURLToPath(new URL('../../../tests/dataset/sachs.csv', import.meta.url))
  const upload = page.waitForResponse((item) => item.url().endsWith('/api/upload_file'))
  await page.locator('.composer input[type=file]').setInputFiles(dataset)
  await data(await upload)
  console.log('Historical interrupt: CSV uploaded to the isolated file library.')

  const created = page.waitForResponse((item) => item.url().endsWith('/api/agent/jobs') && item.request().method() === 'POST')
  const composer = page.locator('.composer textarea')
  await composer.fill('请用 DirectLiNGAM 对上传的连续数值 CSV 做因果分析。目标变量必须是 invalid_edit_anchor，处理变量必须是 praf。请按我指定的列名严格校验。')
  await composer.press('Enter')
  const { job_id: origin } = await data(await created)
  const questionHistory = await waitFor(async () => {
    const history = await load()
    return history.messages[0]?.thinking_after?.status === 'waiting_input' && history.messages[1]?.checkpoint_id && history
  }, 'Real fold node did not publish an interrupt checkpoint.')
  const question = questionHistory.messages[1]
  console.log('Historical interrupt: original fold question persisted.')

  const resumed = page.waitForResponse((item) => item.url().endsWith('/resume'))
  await composer.fill('请用 DirectLiNGAM 对上传的 CSV 执行因果分析并生成报告。目标变量为 pmek，处理变量为 praf。旧回答标记 ORIGINAL_ANSWER_77。')
  await composer.press('Enter')
  const original = await data(await resumed)
  assert.equal(original.job_id, origin)

  async function completed(job) {
    const history = await waitFor(async () => {
      const history = await load()
      const input = history.messages.find((message) => message.analysis_job_id === job && message.thinking_after?.status === 'completed')
      return input && history.messages.some((message) => message.sender === 'ai' && message.analysis_job_id === job && message.text?.type === 'report') && history
    }, 'The real Worker did not publish an analysis report for this answer.')
    const response = history.messages.find((message) => message.sender === 'ai' && message.analysis_job_id === job && message.text?.type === 'report')
    await page.locator(`[data-message-id="${response.message_id}"]`).waitFor()
    return history
  }

  await completed(origin)
  console.log('Historical interrupt: original answer completed with a report.')

  async function branch(id, message) {
    await page.locator(`[data-message-id="${id}"] .edit-message-button`).click()
    const editor = page.getByRole('textbox', { name: '编辑消息', exact: true })
    await editor.fill(message)
    const sent = page.waitForResponse((item) => item.url().endsWith('/edit-resend'))
    await editor.press('Enter')
    const result = await data(await sent)
    assert.equal(result.execution_mode, 'resume_checkpoint')
    assert.notEqual(result.job_id, origin)
    const history = await completed(result.job_id)
    assert.equal(history.messages.length, 4)
    assert.equal(history.messages[1].message_id, question.message_id)
    assert.equal(history.messages[1].checkpoint_id, question.checkpoint_id)
    assert.equal(history.messages[2].message_id, result.message_id)
    assert.ok(!JSON.stringify(history.messages).includes('ORIGINAL_ANSWER_77'))
    return result
  }
  const a = await branch(original.message_id, '请用 DirectLiNGAM 对上传的 CSV 执行因果分析并生成报告。目标变量为 PKA，处理变量为 praf。分支标记 BRANCH_A_77。')
  console.log('Historical interrupt: branch A completed with a report.')
  const b = await branch(a.message_id, '请用 DirectLiNGAM 对上传的 CSV 执行因果分析并生成报告。目标变量为 P38，处理变量为 pmek。分支标记 BRANCH_B_77。')
  const final = await load()
  assert.ok(!JSON.stringify(final.messages).includes('BRANCH_A_77'))
  assert.equal(final.messages[0].thinking_after?.status, 'completed')
  assert.equal(final.messages[0].thinking_after.last_event_id, questionHistory.messages[0].thinking_after.last_event_id)
  const oldEvents = await context.request.get(`/api/agent/jobs/${origin}/events`, {
    headers: { 'Last-Event-ID': String(final.messages[0].thinking_after.last_event_id) },
  })
  assert.equal(oldEvents.status(), 200)
  assert.equal(await oldEvents.text(), '')
  const superseded = await context.request.get(`/api/agent/jobs/${a.job_id}/events`)
  assert.equal(superseded.status(), 404)
  const { new_session_id: newSession } = await data(await context.request.post('/api/new_chat'))
  const fresh = await data(await context.request.get(`/api/load_session?session=${newSession}`))
  assert.deepEqual(fresh.messages, [])
  assert.equal(fresh.conversation_version, 0)
  console.log(`Historical interrupt: branches, original question and old SSE visibility passed. Session=${session} branches=${a.job_id},${b.job_id}`)
} finally {
  await browser.close()
}
