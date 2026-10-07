import { isApiError } from '../../api/errors'

export function createIdempotencyKey(): string {
  const cryptoApi = globalThis.crypto
  if (cryptoApi && typeof cryptoApi.randomUUID === 'function') return cryptoApi.randomUUID()
  if (cryptoApi && typeof cryptoApi.getRandomValues === 'function') {
    const bytes = new Uint8Array(16)
    cryptoApi.getRandomValues(bytes)
    bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40
    bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
  }
  throw new Error('当前浏览器不支持安全的请求幂等键生成')
}

export interface RetryOptions {
  maxRetries?: number
  sleep?: (milliseconds: number) => Promise<void>
}

function isRetryable(error: unknown): boolean {
  if (!isApiError(error)) return true
  return error.status === null || error.status >= 500
}

export async function retryIdempotent<T>(
  request: () => Promise<T>,
  options: RetryOptions = {},
): Promise<T> {
  const maxRetries = options.maxRetries ?? 1
  const sleep = options.sleep ?? ((milliseconds: number) => new Promise<void>((resolve) => {
    globalThis.setTimeout(resolve, milliseconds)
  }))
  let retryCount = 0
  while (true) {
    try {
      return await request()
    } catch (error) {
      if (!isRetryable(error) || retryCount >= maxRetries) throw error
      retryCount += 1
      await sleep(100 * retryCount)
    }
  }
}
