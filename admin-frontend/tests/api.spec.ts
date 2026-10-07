import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  adminAuthRedirectOptions,
  adminApi,
  adminLoginUrl,
  ApiError,
  loadIdentity,
  shouldRedirectForApiError,
} from '../src/api'

function jsonResponse(body: unknown, status = 200, requestId = 'response-request-id'): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'X-Request-ID': requestId }),
    json: async () => body,
  } as Response
}

describe('管理员类型化 API 客户端', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  it('从 check_auth 保存 Session CSRF，并附加到管理员写请求', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(jsonResponse({
        isLoggedIn: true,
        username: 'admin',
        role: 'admin',
        csrf_token: 'csrf-value',
      }))
      .mockResolvedValueOnce(jsonResponse({
        success: true,
        data: {
          groups: ['realtime', 'sql_performance', 'capacity'],
          requested_at: '2026-07-25T00:00:00Z',
        },
      }))

    await loadIdentity()
    await adminApi.refresh()

    const [, options] = fetchMock.mock.calls[1]
    const headers = new Headers(options?.headers)
    expect(options?.method).toBe('POST')
    expect(headers.get('X-CSRF-Token')).toBe('csrf-value')
  })

  it('错误对象优先保留服务端 request ID 和字段错误', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(jsonResponse({
        isLoggedIn: true,
        username: 'admin',
        role: 'admin',
        csrf_token: 'csrf-value',
      }))
      .mockResolvedValueOnce(jsonResponse({
        success: false,
        error: '校验失败',
        code: 'validation_error',
        request_id: 'payload-request-id',
        fields: { realtime_interval_seconds: '必须在 5 到 10 之间' },
      }, 400))

    await loadIdentity()
    const rejection = adminApi.saveSettings(1, {
      auto_refresh_enabled: null,
      realtime_interval_seconds: 4,
      sql_interval_seconds: null,
      table_capacity_interval_seconds: null,
      slow_query_warning_delta: null,
      integrity_enabled: null,
      integrity_interval_seconds: null,
    })

    await expect(rejection).rejects.toMatchObject({
      name: 'ApiError',
      status: 400,
      code: 'validation_error',
      requestId: 'payload-request-id',
      fields: { realtime_interval_seconds: '必须在 5 到 10 之间' },
    } satisfies Partial<ApiError>)
  })

  it('业务列表编码筛选参数且正文接口显式携带 offset', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(jsonResponse({
        success: true,
        data: { items: [], limit: 20, has_more: false, next_cursor: null },
      }))
      .mockResolvedValueOnce(jsonResponse({
        success: true,
        data: {
          kind: 'message',
          content: '下一块',
          offset: 65536,
          returned_length: 3,
          total_length: 65539,
          next_offset: null,
          truncated: false,
        },
      }))

    await adminApi.users({ q: '张 三', role: 'admin', is_active: false })
    await adminApi.messageContent(12, 65536)

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      '/api/admin/business/users?q=%E5%BC%A0+%E4%B8%89&role=admin&is_active=false',
    )
    expect(String(fetchMock.mock.calls[1][0])).toBe(
      '/api/admin/business/messages/12/content?offset=65536',
    )
  })

  it('deep 审计登记请求携带 CSRF 且固定为手动 deep 模式', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(jsonResponse({
        isLoggedIn: true,
        username: 'admin',
        role: 'admin',
        csrf_token: 'deep-csrf',
      }))
      .mockResolvedValueOnce(jsonResponse({
        success: true,
        data: { groups: ['deep_audit'], requested_at: '2026-07-26T12:00:00Z' },
      }, 202))

    await loadIdentity()
    await adminApi.runDeepAudit()

    const [, options] = fetchMock.mock.calls[1]
    expect(options?.method).toBe('POST')
    expect(new Headers(options?.headers).get('X-CSRF-Token')).toBe('deep-csrf')
    expect(options?.body).toBe(JSON.stringify({ mode: 'deep' }))
  })

  it('重新认证密码错误留在当前弹窗，只有 Session 或管理员权限失效才跳转', () => {
    expect(shouldRedirectForApiError(401, 'reauth_failed')).toBe(false)
    expect(shouldRedirectForApiError(401, 'reauth_required')).toBe(false)
    expect(shouldRedirectForApiError(403, 'csrf_invalid')).toBe(false)
    expect(shouldRedirectForApiError(401, 'auth_required')).toBe(true)
    expect(shouldRedirectForApiError(403, 'admin_required')).toBe(true)
  })

  it('Session 失效保留管理页面，角色越权只返回普通首页提示', () => {
    expect(
      adminAuthRedirectOptions(401, 'auth_required', '/admin/database/settings'),
    ).toEqual({ next: '/admin/database/settings' })
    expect(
      adminAuthRedirectOptions(401, 'auth_required', '/unexpected'),
    ).toEqual({ next: '/admin/database' })
    expect(
      adminAuthRedirectOptions(403, 'admin_required', '/admin/users'),
    ).toEqual({ notice: 'admin_required' })
    expect(adminAuthRedirectOptions(401, 'reauth_failed', '/admin/users')).toBeNull()
  })

  it('统一登录入口固定在 /auth/sign-in，并编码回跳与提示', () => {
    expect(adminLoginUrl()).toBe('/auth/sign-in')
    expect(adminLoginUrl({ next: '/admin/database' })).toBe('/auth/sign-in?next=%2Fadmin%2Fdatabase')
    expect(adminLoginUrl({ notice: 'admin_required' })).toBe('/auth/sign-in?notice=admin_required')
  })

  it('受控改密只在 JSON 请求体传递密码，并同时携带 CSRF 与幂等键', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(jsonResponse({
        isLoggedIn: true,
        username: 'admin',
        role: 'admin',
        csrf_token: 'write-csrf',
      }))
      .mockResolvedValueOnce(jsonResponse({
        success: true,
        data: {
          operation_id: 'operation-1',
          operation_type: 'user_set_password',
          target_count: 2,
          replayed: false,
          items: [],
        },
      }))

    await loadIdentity()
    await adminApi.executeUserOperation({
      action: 'set_password',
      target_ids: [7, 8],
      new_password: 'a sufficiently long password',
      reauth_password: 'current admin password',
      confirmed: true,
    }, 'idempotency-key-1')

    const [url, options] = fetchMock.mock.calls[1]
    const headers = new Headers(options?.headers)
    expect(String(url)).toBe('/api/admin/business/users/operations')
    expect(String(url)).not.toContain('password')
    expect(options?.method).toBe('POST')
    expect(headers.get('X-CSRF-Token')).toBe('write-csrf')
    expect(headers.get('Idempotency-Key')).toBe('idempotency-key-1')
    expect(JSON.parse(String(options?.body))).toMatchObject({
      action: 'set_password',
      target_ids: [7, 8],
      new_password: 'a sufficiently long password',
      reauth_password: 'current admin password',
      confirmed: true,
    })
  })
})
