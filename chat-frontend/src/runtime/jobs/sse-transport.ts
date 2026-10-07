import { ApiError, errorMessageForStatus, fallbackErrorCode } from '../../api/errors'
import { consumeSseResponse, SseProtocolError } from '../../api/events.schemas'
import type { DecodedSseEvent } from '../../api/events.schemas'

export type TransportConnectionState = 'connecting' | 'open' | 'reconnecting' | 'closed'
export type FetchImplementation = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>

export interface SseTransportOptions {
  initialCursor: number
  signal: AbortSignal
  getCursor: () => number
  shouldStop: () => boolean
  maxReconnectAttempts?: number
  fetchImpl?: FetchImplementation
  sleep?: (milliseconds: number, signal: AbortSignal) => Promise<void>
  onConnectionState?: (state: TransportConnectionState) => void
}

export class SseTransportError extends Error {
  readonly code = 'sse_transport_error'

  constructor(message: string, options?: { cause?: unknown }) {
    super(message, options)
    this.name = 'SseTransportError'
  }
}

function abortError(): DOMException {
  return new DOMException('SSE 订阅已取消', 'AbortError')
}

function isAbortError(error: unknown): boolean {
  return typeof error === 'object'
    && error !== null
    && 'name' in error
    && error.name === 'AbortError'
}

function shouldRetry(error: unknown): boolean {
  if (error instanceof ApiError) return error.status === null || error.status >= 500
  return true
}

function defaultSleep(milliseconds: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(abortError())
  return new Promise<void>((resolve, reject) => {
    const onAbort = () => {
      globalThis.clearTimeout(timer)
      signal.removeEventListener('abort', onAbort)
      reject(abortError())
    }
    const done = () => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }
    const timer = globalThis.setTimeout(done, milliseconds)
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

async function responseError(response: Response): Promise<ApiError> {
  let payload: unknown = null
  try {
    const text = await response.text()
    if (text) {
      try {
        payload = JSON.parse(text) as unknown
      } catch {
        payload = text
      }
    }
  } catch {
    payload = null
  }
  const message = typeof payload === 'object' && payload !== null && 'error' in payload && typeof payload.error === 'string'
    ? payload.error
    : errorMessageForStatus(response.status)
  const code = typeof payload === 'object' && payload !== null && 'code' in payload && typeof payload.code === 'string'
    ? payload.code
    : fallbackErrorCode(response.status)
  return new ApiError({
    status: response.status,
    code,
    message,
    requestId: response.headers.get('X-Request-ID'),
    details: payload,
  })
}

export class JobSseTransport {
  async stream(
    jobId: string,
    options: SseTransportOptions,
    onEvent: (event: DecodedSseEvent) => Promise<void> | void,
  ): Promise<void> {
    const fetchImpl = options.fetchImpl ?? fetch
    const sleep = options.sleep ?? defaultSleep
    const maxReconnectAttempts = options.maxReconnectAttempts ?? 3
    let firstConnection = true
    let reconnectAttempts = 0

    while (!options.signal.aborted) {
      const isInitial = firstConnection
      firstConnection = false
      const cursor = options.getCursor() || options.initialCursor
      const query = isInitial && cursor > 0 ? `?last_event_id=${encodeURIComponent(cursor)}` : ''
      const headers: HeadersInit = { Accept: 'text/event-stream' }
      if (!isInitial && cursor > 0) headers['Last-Event-ID'] = String(cursor)
      options.onConnectionState?.(isInitial ? 'connecting' : 'reconnecting')

      try {
        const response = await fetchImpl(`/api/agent/jobs/${encodeURIComponent(jobId)}/events${query}`, {
          method: 'GET',
          credentials: 'same-origin',
          headers,
          signal: options.signal,
        })
        if (!response.ok) throw await responseError(response)
        options.onConnectionState?.('open')
        await consumeSseResponse(response, onEvent)
        if (options.shouldStop()) {
          options.onConnectionState?.('closed')
          return
        }
        throw new SseTransportError('SSE 连接在任务终态前关闭')
      } catch (error) {
        if (options.signal.aborted || isAbortError(error)) {
          options.onConnectionState?.('closed')
          return
        }
        if (error instanceof SseProtocolError) {
          options.onConnectionState?.('closed')
          throw error
        }
        if (!shouldRetry(error)) {
          options.onConnectionState?.('closed')
          throw error
        }
        if (reconnectAttempts >= maxReconnectAttempts) {
          options.onConnectionState?.('closed')
          throw error
        }
        reconnectAttempts += 1
        options.onConnectionState?.('reconnecting')
        try {
          await sleep(100 * 2 ** (reconnectAttempts - 1), options.signal)
        } catch (sleepError) {
          if (options.signal.aborted || isAbortError(sleepError)) {
            options.onConnectionState?.('closed')
            return
          }
          throw sleepError
        }
      }
    }
    options.onConnectionState?.('closed')
  }
}
