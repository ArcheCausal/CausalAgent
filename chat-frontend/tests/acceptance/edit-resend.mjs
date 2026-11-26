/* Manual acceptance against an isolated deployment with a real worker and model. */
import assert from 'node:assert/strict'
import console from 'node:console'
import { randomUUID } from 'node:crypto'
import { createServer, request } from 'node:http'
import { once } from 'node:events'
import process from 'node:process'
import { setTimeout } from 'node:timers/promises'
import { URL } from 'node:url'
import { chromium } from '@playwright/test'

const baseURL = process.env.CHAT_EDIT_TEST_URL
if (!baseURL) throw new Error('Set CHAT_EDIT_TEST_URL to the isolated deployment.')
const upstream = new URL(baseURL)
let breakNextStream = false
let brokenStream = null
let reconnect = null
const proxy = createServer((incoming, outgoing) => {
  const editRequest = request(new URL(incoming.url, upstream), {
    method: incoming.method,
    headers: incoming.headers,
  }, (response) => {
    const firstStream = breakNextStream && incoming.url.endsWith('/events') && response.statusCode === 200
    outgoing.writeHead(response.statusCode, response.headers)
    if (!firstStream) {
      response.pipe(outgoing)
      return
    }
    breakNextStream = false
    let buffer = ''
    response.setEncoding('utf8')
    response.on('data', (chunk) => {
      if (brokenStream) return
      buffer += chunk
      const end = buffer.indexOf('\n\n')
      const id = end >= 0 ? /^id: (\d+)$/m.exec(buffer.slice(0, end)) : null
      if (id) {
        brokenStream = { path: incoming.url, id: id[1] }
        outgoing.write(buffer.slice(0, end + 2), () => {
          void setTimeout(75).then(() => {
            outgoing.destroy()
            response.destroy()
          })
        })
      }
    })
    response.on('end', () => {
      if (!brokenStream) outgoing.end(buffer)
    })
  })
  if (brokenStream?.path === incoming.url && incoming.headers['last-event-id']) {
    reconnect = incoming.headers['last-event-id']
  }
  outgoing.on('close', () => editRequest.destroy())
  editRequest.on('error', () => outgoing.destroy())
  incoming.pipe(editRequest)
})
proxy.listen(0, '127.0.0.1')
await once(proxy, 'listening')
const browser = await chromium.launch({ channel: process.env.CHAT_EDIT_BROWSER_CHANNEL })
const context = await browser.newContext({ baseURL: 'http://127.0.0.1:' + proxy.address().port })

async function json(response) {
  assert.ok(response.ok(), `HTTP ${response.status()}`)
  return response.json()
}

async function eventually(check, timeout = 120_000) {
  const until = Date.now() + timeout
  while (Date.now() < until) {
    const result = await check()
    if (result) return result
    await setTimeout(500)
  }
  throw new Error('Timed out waiting for persisted conversation.')
}

try {
  const username = `edit_${randomUUID().slice(0, 12)}`
  const password = `A8${randomUUID()}`
  await json(await context.request.post('/api/register', { data: { username, password } }))
  const auth = await json(await context.request.post('/api/login', { data: { username, password } }))
  const { new_session_id: session } = await json(await context.request.post('/api/new_chat'))
  const page = await context.newPage()
  const secondTab = await context.newPage()
  const url = `/dashboard/session/${session}`
  await page.goto(url)
  const load = async () => json(await context.request.get(`/api/load_session?session=${session}`))
  const send = async (message) => {
    const response = page.waitForResponse((item) => item.url().endsWith('/api/agent/jobs') && item.request().method() === 'POST')
    await page.locator('.composer textarea').fill(message)
    await page.locator('.composer textarea').press('Enter')
    const { job_id: job, message_id: messageId } = await json(await response)
    await eventually(async () => (await load()).messages.some((item) => item.sender === 'ai' && item.analysis_job_id === job))
    await page.locator(`[data-message-id="${messageId}"] .edit-message-button`).waitFor()
    return { job, messageId }
  }
  const first = await send('请简单解释什么是因果关系。')
  const middle = await send('请用一句话解释混杂因素。')
  const last = await send('请用一句话解释因果图。')
  await eventually(async () => (await load()).messages.length === 6)
  await secondTab.goto(url)

  async function openEdit(tab, id, text) {
    await tab.locator(`[data-message-id="${id}"] .edit-message-button`).click()
    const editor = tab.getByRole('textbox', { name: '编辑消息', exact: true })
    await editor.fill(text)
    return editor
  }
  const secondDraft = await openEdit(secondTab, middle.messageId, '另一个标签页中的草稿。')
  const firstTry = await openEdit(page, last.messageId, '这次应使用 Esc 取消。')
  await firstTry.press('Escape')
  await firstTry.waitFor({ state: 'detached' })
  const lastEditor = await openEdit(page, last.messageId, '请解释干预。')
  await lastEditor.press('End')
  await lastEditor.press('Shift+Enter')
  assert.match(await lastEditor.inputValue(), /\n$/)
  await lastEditor.type('请只用一句话。')
  await lastEditor.dispatchEvent('compositionstart')
  await lastEditor.dispatchEvent('keydown', { key: 'Enter', isComposing: true })
  await lastEditor.dispatchEvent('compositionend')
  await lastEditor.waitFor({ state: 'visible' })

  async function submit(tab, editor, wantedStatus = 202) {
    const reply = tab.waitForResponse((item) => item.url().endsWith('/edit-resend'))
    await editor.press('Enter')
    const response = await reply
    assert.equal(response.status(), wantedStatus)
    return response
  }

  breakNextStream = true
  const lastResponse = await submit(page, lastEditor)
  const lastEdit = await json(lastResponse)
  const latest = await eventually(async () => {
    const history = await load()
    return history.messages.some((item) => item.sender === 'ai' && item.analysis_job_id === lastEdit.job_id) && history
  })
  assert.equal(latest.messages.length, 6)
  assert.equal(latest.messages[0].message_id, first.messageId)
  assert.equal(latest.messages[2].message_id, middle.messageId)
  assert.equal(latest.messages[4].message_id, lastEdit.message_id)
  await eventually(() => reconnect, 20_000)
  assert.equal(brokenStream.path, '/api/agent/jobs/' + lastEdit.job_id + '/events')
  assert.equal(reconnect, brokenStream.id)

  const sameRequest = lastResponse.request()
  const duplicate = await context.request.post(sameRequest.url(), {
    headers: {
      'X-CSRF-Token': auth.csrf_token,
      'Idempotency-Key': sameRequest.headers()['idempotency-key'],
    },
    data: sameRequest.postDataJSON(),
  })
  assert.equal(duplicate.status(), 200)
  assert.equal((await duplicate.json()).job_id, lastEdit.job_id)

  const stale = await submit(secondTab, secondDraft, 409)
  assert.equal((await stale.json()).code, 'conversation_version_conflict')
  assert.equal(await secondDraft.inputValue(), '另一个标签页中的草稿。')
  assert.match(await secondTab.locator('.message-edit-form').innerText(), /替换这条消息及之后的对话/)
  await secondTab.getByRole('button', { name: '刷新并核对历史' }).click()
  await secondDraft.fill('请用一句话解释中介变量。')
  const middleEdit = await json(await submit(secondTab, secondDraft))
  const afterMiddle = await eventually(async () => {
    const history = await load()
    return history.messages.some((item) => item.sender === 'ai' && item.analysis_job_id === middleEdit.job_id) && history
  })
  assert.equal(afterMiddle.messages.length, 4)
  assert.equal(afterMiddle.messages[0].message_id, first.messageId)
  assert.equal(afterMiddle.messages[2].message_id, middleEdit.message_id)

  await page.reload()
  const firstEditor = await openEdit(page, first.messageId, '请只用一句话解释随机实验。')
  const firstEdit = await json(await submit(page, firstEditor))
  const afterFirst = await eventually(async () => {
    const history = await load()
    return history.messages.some((item) => item.sender === 'ai' && item.analysis_job_id === firstEdit.job_id) && history
  })
  assert.deepEqual(afterFirst.messages.map((item) => item.sender), ['user', 'ai'])
  assert.equal(afterFirst.messages[0].message_id, firstEdit.message_id)
  assert.ok(afterFirst.messages[1].checkpoint_id)

  const eventsUrl = `/api/agent/jobs/${firstEdit.job_id}/events`
  const stream = await context.request.get(eventsUrl)
  assert.ok(stream.ok())
  const ids = [...(await stream.text()).matchAll(/^id: (\d+)$/gm)].map((match) => Number(match[1]))
  assert.ok(ids.length > 1)
  const replay = await context.request.get(eventsUrl, { headers: { 'Last-Event-ID': String(ids[0]) } })
  assert.ok(replay.ok())
  assert.deepEqual(
    [...(await replay.text()).matchAll(/^id: (\d+)$/gm)].map((match) => Number(match[1])),
    ids.slice(1),
  )
  console.log('Browser: first, middle, last, Escape, Shift+Enter, composition, stale tab, idempotency and SSE cursor passed.')
  if (process.env.CHAT_EDIT_VERIFY_CLEANUP === '1') {
    const deleted = await json(await context.request.post('/api/delete_session', { data: { session_id: session } }))
    assert.equal(deleted.checkpoint_cleanup, 'pending')
    assert.equal((await context.request.get(`/api/load_session?session=${session}`)).status(), 404)
    console.log(`Cleanup jobs: ${[first.job, middle.job, last.job, lastEdit.job_id, middleEdit.job_id, firstEdit.job_id].join(',')}`)
  }
} finally {
  await browser.close()
  proxy.closeAllConnections()
  proxy.close()
}
