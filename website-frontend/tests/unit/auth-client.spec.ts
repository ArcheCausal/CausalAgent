import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthApiError, isAllowedInternalPath, login, register, resolveRedirect } from '../../src/api/auth'

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('登录回跳白名单', () => {
  it('接受站内已登记页面', () => {
    for (const path of ['/dashboard', '/dashboard/settings', '/dashboard/session/abc-1', '/admin', '/admin/database', '/rag-eval']) {
      expect(isAllowedInternalPath(path)).toBe(true)
    }
  })

  it('拒绝外部地址、协议相对地址和公开页面', () => {
    for (const path of ['https://evil.example/dashboard', '//evil.example/dashboard', '/product', '/', '', null]) {
      expect(isAllowedInternalPath(path)).toBe(false)
    }
    expect(isAllowedInternalPath('/dashboard\\x')).toBe(false)
  })

  it('优先使用服务端返回的回跳地址，其次使用查询参数，最后回到工作区', () => {
    expect(resolveRedirect('/admin/database', '/dashboard')).toBe('/admin/database')
    expect(resolveRedirect('https://evil.example', '/rag-eval')).toBe('/rag-eval')
    expect(resolveRedirect(undefined, null)).toBe('/dashboard')
  })
})

describe('认证接口客户端', () => {
  it('登录请求携带经过校验的 next，并解析服务端返回', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ success: true, username: 'alice', role: 'admin', redirect_to: '/admin/database', csrf_token: 'token' }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const payload = await login('alice', 'secret', '/dashboard')

    expect(payload.role).toBe('admin')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/api/login')
    expect(JSON.parse(String(init.body))).toEqual({ username: 'alice', password: 'secret', next: '/dashboard' })
  })

  it('登录失败时抛出服务端错误信息', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ success: false, error: '密码错误' }, 401)))

    await expect(login('alice', 'bad', null)).rejects.toThrowError(new AuthApiError('密码错误'))
  })

  it('响应不是 JSON 时给出稳定的错误', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html></html>', { status: 500 })))

    await expect(register('alice', 'secret1')).rejects.toThrowError(new AuthApiError('服务器返回了无法识别的响应。'))
  })
})
