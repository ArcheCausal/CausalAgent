import { describe, expect, it, vi } from 'vitest'
import { z } from 'zod'
import { api, requestJson } from '../../src/api/client'
import { ApiError } from '../../src/api/errors'

function jsonResponse(payload: unknown, status = 200, extraHeaders: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'content-type': 'application/json', ...extraHeaders },
  })
}

describe('ordinary chat API contracts', () => {
  it('keeps the tuple session response and job request body shape', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockResolvedValueOnce(jsonResponse([
      ['session-1', { preview: '因果分析', last_time: '2026-09-15 10:00:00', extra: 'preserved by server' }],
    ]))
    expect(await api.listSessions()).toEqual([
      ['session-1', { preview: '因果分析', last_time: '2026-09-15 10:00:00', extra: 'preserved by server' }],
    ])

    fetchMock.mockResolvedValueOnce(jsonResponse({ success: true, job_id: 'job-1', status: 'queued', existing: false }, 202))
    await api.createJob('hello', 'session-1', '00000000-0000-4000-8000-000000000001', 42, true)
    const init = fetchMock.mock.calls[1]?.[1] as RequestInit | undefined
    expect(JSON.parse(String(init?.body))).toEqual({
      message: 'hello',
      session_id: 'session-1',
      input_user_file_id: 42,
      web_search_enabled: true,
    })
    expect(new Headers(init?.headers).get('Idempotency-Key')).toBe('00000000-0000-4000-8000-000000000001')
  })

  it.each([
    [401, 'unauthorized'],
    [403, 'forbidden'],
    [404, 'not_found'],
    [409, 'conflict'],
    [413, 'payload_too_large'],
  ])('normalizes HTTP %s with request ID and stable code', async (status, code) => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ error: 'server detail' }, status, { 'X-Request-ID': 'request-123' }))
    vi.stubGlobal('fetch', fetchMock)

    const error = await requestJson('/api/test', {}, z.object({ ok: z.boolean() })).catch((cause: unknown) => cause)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status, code, requestId: 'request-123', message: 'server detail' })
  })

  it('rejects a successful response that does not match the declared schema', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ success: true })))
    const error = await api.listSessions().catch((cause: unknown) => cause)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ code: 'invalid_response', status: 200 })
  })

  it('accepts legacy chat rows that explicitly return null Job references', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      success: true,
      messages: [{
        sender: 'user',
        text: '历史消息',
        analysis_job_id: null,
        analysis_job_input_id: null,
      }],
    })))

    await expect(api.loadSession('session-1')).resolves.toMatchObject({ success: true })
  })
})
