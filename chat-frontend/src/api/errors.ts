export interface ApiErrorOptions {
  status: number | null
  code: string
  message: string
  requestId: string | null
  details?: unknown
  cause?: unknown
}

export class ApiError extends Error {
  readonly status: number | null
  readonly code: string
  readonly requestId: string | null
  readonly details: unknown

  constructor(options: ApiErrorOptions) {
    super(options.message, { cause: options.cause })
    this.name = 'ApiError'
    this.status = options.status
    this.code = options.code
    this.requestId = options.requestId
    this.details = options.details
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}

export function fallbackErrorCode(status: number | null): string {
  if (status === 400) return 'bad_request'
  if (status === 401) return 'unauthorized'
  if (status === 403) return 'forbidden'
  if (status === 404) return 'not_found'
  if (status === 409) return 'conflict'
  if (status === 413) return 'payload_too_large'
  if (status !== null && status >= 500) return 'server_error'
  return 'network_error'
}

export function errorMessageForStatus(status: number | null): string {
  if (status === 401) return '登录状态已失效，请重新登录。'
  if (status === 403) return '当前账号没有权限执行此操作。'
  if (status === 404) return '请求的资源不存在或无权访问。'
  if (status === 409) return '当前状态不允许执行此操作。'
  if (status === 413) return '请求内容超过大小限制。'
  if (status !== null && status >= 500) return '服务器暂时不可用，请稍后重试。'
  return '网络请求失败，请稍后重试。'
}
