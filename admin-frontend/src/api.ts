import type {
  AdminAttachment,
  AgentWorkerSummary,
  AdminCheckpointPage,
  AdminFile,
  AdminJob,
  AdminJobEvent,
  AdminMessage,
  AdminOperationResult,
  AdminSession,
  AdminUser,
  AuditEvent,
  BusinessOverview,
  CsvPreview,
  CursorPage,
  DashboardData,
  DeepAuditSnapshot,
  FileDeleteImpact,
  Identity,
  MonitorOverrideMap,
  MonitorSettings,
  QuickAuditSnapshot,
  RuntimeConfig,
  SensitiveContentChunk,
  UserDeleteImpact,
  UserOperationAction,
  UserOperationPreview,
} from './types'

interface ApiEnvelope<T> {
  success: boolean
  data: T
  error?: string
  code?: string
  request_id?: string
  fields?: Record<string, string>
  current?: MonitorSettings
}

export class ApiError extends Error {
  status: number
  code?: string
  requestId?: string
  fields?: Record<string, string>
  current?: MonitorSettings

  constructor(
    message: string,
    status: number,
    payload: Partial<ApiEnvelope<unknown>> = {},
    responseRequestId?: string | null,
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = payload.code
    this.requestId = payload.request_id || responseRequestId || undefined
    this.fields = payload.fields
    this.current = payload.current
  }
}

let csrfToken = ''

interface LoginRedirectOptions {
  next?: string
  notice?: 'admin_required'
}

/** 返回统一登录入口，并编码受服务端复核的内部导航状态。 */
export function adminLoginUrl(options: LoginRedirectOptions = {}): string {
  const origin = import.meta.env.VITE_FLASK_ORIGIN?.replace(/\/$/, '') || ''
  const params = new URLSearchParams()
  if (options.next) params.set('next', options.next)
  if (options.notice) params.set('notice', options.notice)
  const query = params.toString()
  return `${origin}/auth/sign-in${query ? `?${query}` : ''}`
}

/** 返回当前管理员页面路径，供重新登录后由服务端白名单复核。 */
function currentAdminPath(path = window.location.pathname): string {
  return path === '/admin' || path.startsWith('/admin/')
    ? path
    : '/admin/database'
}

/** 把管理员鉴权错误转换为可验证的统一登录导航参数。 */
export function adminAuthRedirectOptions(
  status: number,
  code: string | undefined,
  path = window.location.pathname,
): LoginRedirectOptions | null {
  if (status === 401 && code === 'auth_required') {
    return { next: currentAdminPath(path) }
  }
  if (status === 403 && code === 'admin_required') {
    return { notice: 'admin_required' }
  }
  return null
}

/** 把管理员会话送回统一登录入口，并区分失效与越权提示。 */
function redirectToLogin(options: LoginRedirectOptions = {}): void {
  window.location.assign(adminLoginUrl(options))
}

/** 按稳定错误码选择重新登录回跳或普通用户无权限提示。 */
function redirectForAdminAuthError(status: number, code?: string): void {
  const options = adminAuthRedirectOptions(status, code)
  if (options) redirectToLogin(options)
}

/** 只把真实 Session 失效或管理员权限失效视为需要离开当前管理页面。 */
export function shouldRedirectForApiError(status: number, code?: string): boolean {
  return (
    (status === 401 && code === 'auth_required') ||
    (status === 403 && code === 'admin_required')
  )
}

/** 强制从后端恢复实时身份，并缓存 Session 绑定的 CSRF token。 */
export async function loadIdentity(): Promise<Identity> {
  const response = await fetch('/api/check_auth', {
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
    cache: 'no-store',
  })
  const data = (await response.json()) as Identity
  if (!data.isLoggedIn || !data.csrf_token) {
    redirectToLogin({ next: currentAdminPath() })
    throw new ApiError('管理员会话无效', response.status || 401)
  }
  if (data.role !== 'admin') {
    redirectToLogin({ notice: 'admin_required' })
    throw new ApiError('需要管理员权限', 403, { code: 'admin_required' })
  }
  csrfToken = data.csrf_token
  return data
}

/** 发送同源管理员 API 请求，并统一处理 CSRF、错误与 request ID。 */
async function apiRequest<T>(
  url: string,
  options: RequestInit = {},
): Promise<T> {
  const method = (options.method || 'GET').toUpperCase()
  const headers = new Headers(options.headers)
  headers.set('Accept', 'application/json')
  if (options.body !== undefined) headers.set('Content-Type', 'application/json')
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    headers.set('X-CSRF-Token', csrfToken)
  }
  const response = await fetch(url, {
    ...options,
    method,
    headers,
    credentials: 'same-origin',
  })
  let payload: Partial<ApiEnvelope<T>>
  try {
    payload = (await response.json()) as Partial<ApiEnvelope<T>>
  } catch {
    throw new ApiError(
      `服务端返回了无效响应 (${response.status})`,
      response.status,
      {},
      response.headers.get('X-Request-ID'),
    )
  }
  if (!response.ok || payload.success === false) {
    const error = new ApiError(
      payload.error || `请求失败 (${response.status})`,
      response.status,
      payload,
      response.headers.get('X-Request-ID'),
    )
    if (shouldRedirectForApiError(response.status, payload.code)) {
      redirectForAdminAuthError(response.status, payload.code)
    }
    throw error
  }
  return payload.data as T
}

type QueryValue = string | number | boolean | null | undefined

/** 把有值的筛选参数编码到管理员 API URL。 */
function withQuery(path: string, values: Record<string, QueryValue>): string {
  const params = new URLSearchParams()
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      params.set(key, String(value))
    }
  })
  const query = params.toString()
  return query ? `${path}?${query}` : path
}

/** 读取受保护下载并使用服务端安全文件名触发浏览器保存。 */
async function downloadRequest(url: string): Promise<void> {
  const response = await fetch(url, {
    credentials: 'same-origin',
    headers: { Accept: 'application/octet-stream' },
    cache: 'no-store',
  })
  if (!response.ok) {
    let payload: Partial<ApiEnvelope<unknown>> = {}
    try {
      payload = (await response.json()) as Partial<ApiEnvelope<unknown>>
    } catch {
      // 非 JSON 下载错误由统一状态文案兜底。
    }
    const error = new ApiError(
      payload.error || `下载失败 (${response.status})`,
      response.status,
      payload,
      response.headers.get('X-Request-ID'),
    )
    if (shouldRedirectForApiError(response.status, payload.code)) {
      redirectForAdminAuthError(response.status, payload.code)
    }
    throw error
  }
  const blob = await response.blob()
  const disposition = response.headers.get('Content-Disposition') || ''
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i)
  const plainMatch = disposition.match(/filename="?([^";]+)"?/i)
  let filename = 'download'
  try {
    filename = decodeURIComponent(utf8Match?.[1] || plainMatch?.[1] || filename)
  } catch {
    filename = plainMatch?.[1] || filename
  }
  const objectUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = filename
  link.hidden = true
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(objectUrl)
}

export const adminApi = {
  /** 读取由部署环境决定的运行期地址。 */
  runtimeConfig: () => apiRequest<RuntimeConfig>(
    '/api/admin/runtime-config',
    { cache: 'no-store' },
  ),
  /** 读取一次完整聚合看板。 */
  dashboard: () => apiRequest<DashboardData>('/api/admin/db/dashboard', { cache: 'no-store' }),
  /** 登记普通共享刷新。 */
  refresh: () => apiRequest<{ groups: string[]; requested_at: string }>(
    '/api/admin/db/refresh',
    { method: 'POST' },
  ),
  /** 登记独立完整性审计。 */
  runIntegrity: () => apiRequest<{ groups: string[]; requested_at: string }>(
    '/api/admin/db/integrity/run',
    { method: 'POST' },
  ),
  /** 读取七项在线配置和逐项来源。 */
  settings: () => apiRequest<MonitorSettings>('/api/admin/db/settings', { cache: 'no-store' }),
  /** 按乐观版本保存完整覆盖快照。 */
  saveSettings: (version: number, overrides: MonitorOverrideMap) =>
    apiRequest<MonitorSettings>('/api/admin/db/settings', {
      method: 'PUT',
      body: JSON.stringify({ version, overrides }),
    }),
  /** 按乐观版本重置全部覆盖。 */
  resetSettings: (version: number) =>
    apiRequest<MonitorSettings>('/api/admin/db/settings/reset', {
      method: 'POST',
      body: JSON.stringify({ version }),
    }),
  /** 按有界游标读取配置审计历史。 */
  settingsHistory: (limit = 20, beforeId?: number) => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (beforeId !== undefined) params.set('before_id', String(beforeId))
    return apiRequest<{ items: AuditEvent[]; next_before_id: number | null }>(
      `/api/admin/db/settings/history?${params}`,
      { cache: 'no-store' },
    )
  },
  /** 读取 3.1 业务概览和共享快照摘要。 */
  businessOverview: () => apiRequest<BusinessOverview>(
    '/api/admin/business/overview',
    { cache: 'no-store' },
  ),
  /** 分页读取脱敏用户。 */
  users: (filters: {
    limit?: number
    cursor?: string
    q?: string
    role?: string
    is_active?: boolean
  } = {}) => apiRequest<CursorPage<AdminUser>>(
    withQuery('/api/admin/business/users', filters),
    { cache: 'no-store' },
  ),
  /** 读取单个用户详情并触发敏感访问审计。 */
  user: (userId: number) => apiRequest<AdminUser>(
    `/api/admin/business/users/${userId}`,
    { cache: 'no-store' },
  ),
  /** 预览单个或批量用户启停、角色和改密影响。 */
  previewUserOperation: (
    action: UserOperationAction,
    targetIds: number[],
    value?: boolean | 'user' | 'admin',
  ) => apiRequest<UserOperationPreview>(
    '/api/admin/business/users/operations/preview',
    {
      method: 'POST',
      body: JSON.stringify({ action, target_ids: targetIds, value }),
    },
  ),
  /** 执行带重新认证和幂等键的用户写操作。 */
  executeUserOperation: (
    body: {
      action: UserOperationAction
      target_ids: number[]
      value?: boolean | 'user' | 'admin'
      new_password?: string
      reauth_password: string
      confirmed: true
    },
    idempotencyKey: string,
  ) => apiRequest<AdminOperationResult>(
    '/api/admin/business/users/operations',
    {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(body),
    },
  ),
  /** 查询受控操作及异步 checkpoint cleanup 聚合状态。 */
  operation: (operationId: string) => apiRequest<AdminOperationResult>(
    `/api/admin/operations/${encodeURIComponent(operationId)}`,
    { cache: 'no-store' },
  ),
  /** 读取用户物理删除的完整生命周期影响。 */
  userDeleteImpact: (userId: number) => apiRequest<UserDeleteImpact>(
    `/api/admin/business/users/${userId}/delete-impact`,
    { cache: 'no-store' },
  ),
  /** 物理删除用户及其受管生命周期数据。 */
  deleteUser: (
    userId: number,
    body: {
      confirm_username: string
      reauth_password: string
      confirmed: true
    },
    idempotencyKey: string,
  ) => apiRequest<AdminOperationResult>(
    `/api/admin/business/users/${userId}`,
    {
      method: 'DELETE',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(body),
    },
  ),
  /** 分页读取会话摘要。 */
  sessions: (filters: {
    limit?: number
    cursor?: string
    q?: string
    user_id?: number
    is_archived?: boolean
  } = {}) => apiRequest<CursorPage<AdminSession>>(
    withQuery('/api/admin/business/sessions', filters),
    { cache: 'no-store' },
  ),
  /** 读取会话元数据。 */
  session: (sessionId: string) => apiRequest<AdminSession>(
    `/api/admin/business/sessions/${encodeURIComponent(sessionId)}`,
    { cache: 'no-store' },
  ),
  /** 分页读取会话消息摘要。 */
  sessionMessages: (
    sessionId: string,
    filters: { limit?: number; cursor?: string; message_type?: string } = {},
  ) => apiRequest<CursorPage<AdminMessage>>(
    withQuery(
      `/api/admin/business/sessions/${encodeURIComponent(sessionId)}/messages`,
      filters,
    ),
    { cache: 'no-store' },
  ),
  /** 读取消息附件元数据。 */
  messageAttachments: (messageId: number) => apiRequest<{ items: AdminAttachment[] }>(
    `/api/admin/business/messages/${messageId}/attachments`,
    { cache: 'no-store' },
  ),
  /** 点击后分块读取消息正文。 */
  messageContent: (messageId: number, offset = 0) =>
    apiRequest<SensitiveContentChunk>(
      withQuery(`/api/admin/business/messages/${messageId}/content`, { offset }),
      { cache: 'no-store' },
    ),
  /** 点击后分块读取附件正文。 */
  attachmentContent: (attachmentId: number, offset = 0) =>
    apiRequest<SensitiveContentChunk>(
      withQuery(`/api/admin/business/attachments/${attachmentId}/content`, { offset }),
      { cache: 'no-store' },
    ),
  /** 分页读取分析任务摘要。 */
  jobs: (filters: {
    limit?: number
    cursor?: string
    q?: string
    status?: string
    user_id?: number
    session_id?: string
  } = {}) => apiRequest<CursorPage<AdminJob>>(
    withQuery('/api/admin/business/jobs', filters),
    { cache: 'no-store' },
  ),
  /** 读取分析任务页上方的 Agent Worker/Job 汇总。 */
  jobWorkersSummary: async (): Promise<AgentWorkerSummary> => {
    // 复用兼容的 /jobs/workers 响应；该接口的 summary/meta 保留在 envelope 顶层。
    const response = await fetch('/api/admin/jobs/workers', {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    })
    let payload: {
      success?: boolean
      data?: Record<string, unknown>[]
      summary?: AgentWorkerSummary['summary']
      meta?: AgentWorkerSummary['meta']
      error?: string
      code?: string
      request_id?: string
    }
    try {
      payload = await response.json() as typeof payload
    } catch {
      throw new ApiError(
        `服务端返回了无效响应 (${response.status})`,
        response.status,
        {},
        response.headers.get('X-Request-ID'),
      )
    }
    if (!response.ok || payload.success === false) {
      const error = new ApiError(
        payload.error || `请求失败 (${response.status})`,
        response.status,
        payload,
        response.headers.get('X-Request-ID'),
      )
      if (shouldRedirectForApiError(response.status, payload.code)) {
        redirectForAdminAuthError(response.status, payload.code)
      }
      throw error
    }
    return {
      jobs: payload.data || [],
      summary: payload.summary || {},
      meta: payload.meta || {},
    }
  },
  /** 读取任务元数据。 */
  job: (jobId: string) => apiRequest<AdminJob>(
    `/api/admin/business/jobs/${encodeURIComponent(jobId)}`,
    { cache: 'no-store' },
  ),
  /** 分页读取任务事件时间线。 */
  jobEvents: (
    jobId: string,
    filters: { limit?: number; cursor?: string } = {},
  ) => apiRequest<CursorPage<AdminJobEvent>>(
    withQuery(`/api/admin/business/jobs/${encodeURIComponent(jobId)}/events`, filters),
    { cache: 'no-store' },
  ),
  /** 分页读取精确归属当前任务的 PostgreSQL checkpoint 安全摘要。 */
  jobCheckpoints: (
    jobId: string,
    filters: { limit?: number; cursor?: string } = {},
  ) => apiRequest<AdminCheckpointPage>(
    withQuery(
      `/api/admin/business/jobs/${encodeURIComponent(jobId)}/checkpoints`,
      filters,
    ),
    { cache: 'no-store' },
  ),
  /** 点击后分块读取任务输入、结果或错误正文。 */
  jobContent: (
    jobId: string,
    kind: 'input' | 'result' | 'error',
    offset = 0,
    sequence?: number,
  ) => apiRequest<SensitiveContentChunk>(
    withQuery(`/api/admin/business/jobs/${encodeURIComponent(jobId)}/content`, {
      kind,
      offset,
      sequence,
    }),
    { cache: 'no-store' },
  ),
  /** 分页读取文件元数据。 */
  files: (filters: {
    limit?: number
    cursor?: string
    q?: string
    user_id?: number
    mime_type?: string
  } = {}) => apiRequest<CursorPage<AdminFile>>(
    withQuery('/api/admin/business/files', filters),
    { cache: 'no-store' },
  ),
  /** 读取文件元数据。 */
  file: (fileId: number) => apiRequest<AdminFile>(
    `/api/admin/business/files/${fileId}`,
    { cache: 'no-store' },
  ),
  /** 安全预览 CSV，并由后端原子记录访问。 */
  previewFile: (fileId: number) => apiRequest<CsvPreview>(
    `/api/admin/business/files/${fileId}/preview`,
    { cache: 'no-store' },
  ),
  /** 受控下载文件，并由后端原子记录访问。 */
  downloadFile: (fileId: number) =>
    downloadRequest(`/api/admin/business/files/${fileId}/download`),
  /** 读取文件行、BLOB 和活动任务阻断预览。 */
  fileDeleteImpact: (fileId: number) => apiRequest<FileDeleteImpact>(
    `/api/admin/business/files/${fileId}/delete-impact`,
    { cache: 'no-store' },
  ),
  /** 物理删除文件行和 BLOB，不提供回收站。 */
  deleteFile: (
    fileId: number,
    body: {
      confirm_filename: string
      reauth_password: string
      confirmed: true
    },
    idempotencyKey: string,
  ) => apiRequest<AdminOperationResult>(
    `/api/admin/business/files/${fileId}`,
    {
      method: 'DELETE',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(body),
    },
  ),
  /** 读取现有 quick 完整性共享快照。 */
  quickAudit: () => apiRequest<QuickAuditSnapshot>(
    '/api/admin/db/audit?mode=quick',
    { cache: 'no-store' },
  ),
  /** 读取最近一次手动 deep 审计共享快照。 */
  deepAudit: () => apiRequest<DeepAuditSnapshot>(
    '/api/admin/db/audit?mode=deep',
    { cache: 'no-store' },
  ),
  /** 登记 deep 审计请求，真正采集由 monitor 执行。 */
  runDeepAudit: () => apiRequest<{ groups: string[]; requested_at: string }>(
    '/api/admin/db/audit/run',
    {
      method: 'POST',
      body: JSON.stringify({ mode: 'deep' }),
    },
  ),
  /** 清空后端 Session 并回到统一登录入口。 */
  logout: async () => {
    await fetch('/api/logout', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    })
    redirectToLogin()
  },
}
