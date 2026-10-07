import { describe, expect, it, vi } from 'vitest'
import {
  consumeSseResponse,
  decodeSseBlock,
  SseParser,
  SseProtocolError,
} from '../../src/api/events.schemas'
import { JobSseTransport } from '../../src/runtime/jobs/sse-transport'
import { responseFromBytes, responseFromChunks } from '../fixtures/sse'

describe('SSE fetch parser', () => {
  it('handles chunks, comments, CRLF, blank lines, multiline data and custom event names', () => {
    const parser = new SseParser()
    const first = parser.push(': keepalive\r\nid: 7\r\nevent: custom_event\r\ndata: {"type":"custom_event",')
    const second = parser.push('\r\ndata: "value":1}\r\n\r\nid: 8\r\nevent: node_start\r\ndata: {"type":"node_start","step_id":"s1","node_name":"load","title":"Load"}\r\n\r\n')
    const blocks = [...first, ...second, ...parser.finish()]

    expect(blocks).toHaveLength(2)
    expect(blocks[0]).toMatchObject({ event: 'custom_event', id: '7', data: '{"type":"custom_event",\n"value":1}' })
    expect(blocks[1]).toMatchObject({ event: 'node_start', id: '8' })
    expect(decodeSseBlock(blocks[0]!)).toMatchObject({ event: 'custom_event', id: 7, known: false })
    expect(decodeSseBlock(blocks[1]!)).toMatchObject({ event: 'node_start', id: 8, known: true })
  })

  it('preserves UTF-8 text across ReadableStream chunk boundaries', async () => {
    const payload = 'id: 1\nevent: text_delta\ndata: {"type":"text_delta","step_id":"s1","stream_id":"answer","sequence":1,"delta":"因果"}\n\n'
    const bytes = new TextEncoder().encode(payload)
    const response = responseFromBytes([bytes.slice(0, 67), bytes.slice(67, 69), bytes.slice(69)])
    const events: Array<{ id: number | null; data: { type: string; delta?: string } }> = []
    await consumeSseResponse(response, (event) => { events.push(event) })

    expect(events).toHaveLength(1)
    expect(events[0]).toMatchObject({ id: 1, data: { type: 'text_delta', delta: '因果' } })
  })

  it.each([
    ['event/type mismatch', { event: 'progress', id: '1', hasIdField: true, data: '{"type":"decision"}' }],
    ['invalid JSON', { event: 'progress', id: '1', hasIdField: true, data: '{' }],
    ['known event without id', { event: 'progress', id: null, hasIdField: false, data: '{"type":"progress","step_id":"s1","node_name":"n","title":"T","summary":"ok"}' }],
    ['unknown event without id', { event: 'custom_event', id: null, hasIdField: false, data: '{"type":"custom_event"}' }],
    ['invalid id', { event: 'progress', id: 'not-a-number', hasIdField: true, data: '{"type":"progress","step_id":"s1","node_name":"n","title":"T","summary":"ok"}' }],
  ])('rejects %s without yielding a decoded event', (_name, raw) => {
    expect(() => decodeSseBlock(raw)).toThrow(SseProtocolError)
  })
})

describe('SSE fetch transport', () => {
  it('uses the initial query cursor, then Last-Event-ID, and reconnects within a bound', async () => {
    const transport = new JobSseTransport()
    const controller = new AbortController()
    let cursor = 4
    let stopped = false
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(responseFromChunks([
        'id: 5\nevent: progress\ndata: {"type":"progress","step_id":"s1","node_name":"n","title":"T","summary":"first"}\n\n',
      ]))
      .mockResolvedValueOnce(responseFromChunks([
        'id: 6\nevent: final_result\ndata: {"type":"final_result","data":{"type":"text","summary":"done"}}\n\n',
      ]))

    await transport.stream('job-1', {
      initialCursor: 4,
      signal: controller.signal,
      getCursor: () => cursor,
      shouldStop: () => stopped,
      fetchImpl,
      sleep: vi.fn(async () => undefined),
    }, (event) => {
      if (event.id !== null) cursor = event.id
      if (event.data.type === 'final_result') stopped = true
    })

    expect(fetchImpl).toHaveBeenCalledTimes(2)
    expect(fetchImpl.mock.calls[0]?.[0]).toBe('/api/agent/jobs/job-1/events?last_event_id=4')
    const secondInit = fetchImpl.mock.calls[1]?.[1] as RequestInit | undefined
    expect(new Headers(secondInit?.headers).get('Last-Event-ID')).toBe('5')
  })

  it('does not retry a protocol error and stops after the reconnect budget', async () => {
    const protocolFetch = vi.fn().mockResolvedValue(responseFromChunks([
      'id: 1\nevent: progress\ndata: {"type":"progress"}\n\n',
    ]))
    const protocolSleep = vi.fn(async () => undefined)
    await expect(new JobSseTransport().stream('job-1', {
      initialCursor: 0,
      signal: new AbortController().signal,
      getCursor: () => 0,
      shouldStop: () => false,
      fetchImpl: protocolFetch,
      sleep: protocolSleep,
    }, () => undefined)).rejects.toBeInstanceOf(SseProtocolError)
    expect(protocolFetch).toHaveBeenCalledTimes(1)
    expect(protocolSleep).not.toHaveBeenCalled()

    const unavailableFetch = vi.fn().mockRejectedValue(new Error('connection refused'))
    const unavailableSleep = vi.fn(async () => undefined)
    await expect(new JobSseTransport().stream('job-1', {
      initialCursor: 0,
      signal: new AbortController().signal,
      getCursor: () => 0,
      shouldStop: () => false,
      maxReconnectAttempts: 2,
      fetchImpl: unavailableFetch,
      sleep: unavailableSleep,
    }, () => undefined)).rejects.toThrow('connection refused')
    expect(unavailableFetch).toHaveBeenCalledTimes(3)
    expect(unavailableSleep).toHaveBeenCalledTimes(2)
  })

  it('does not retry non-retryable HTTP responses', async () => {
    const unauthorizedFetch = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ error: '登录状态已失效' }),
      { status: 401, headers: { 'content-type': 'application/json' } },
    ))
    const sleep = vi.fn(async () => undefined)

    await expect(new JobSseTransport().stream('job-1', {
      initialCursor: 0,
      signal: new AbortController().signal,
      getCursor: () => 0,
      shouldStop: () => false,
      fetchImpl: unauthorizedFetch,
      sleep,
    }, () => undefined)).rejects.toMatchObject({ status: 401, code: 'unauthorized' })
    expect(unauthorizedFetch).toHaveBeenCalledTimes(1)
    expect(sleep).not.toHaveBeenCalled()
  })
})
