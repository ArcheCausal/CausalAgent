import { z } from 'zod'

/* 官网只消费认证接口：登录、注册和退出都使用同源 Cookie Session。 */

export const INTERNAL_PATH_PREFIXES: readonly string[] = ['/dashboard', '/admin', '/rag-eval']

const loginResponseSchema = z.object({
  success: z.boolean(),
  username: z.string().optional(),
  role: z.string().optional(),
  csrf_token: z.string().optional(),
  redirect_to: z.string().optional(),
  error: z.string().optional(),
})

const simpleResponseSchema = z.object({
  success: z.boolean(),
  error: z.string().optional(),
})

export type LoginResponse = z.infer<typeof loginResponseSchema>

export class AuthApiError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'AuthApiError'
  }
}

export function isAllowedInternalPath(candidate: string | null | undefined): boolean {
  if (!candidate) return false
  if (!candidate.startsWith('/')) return false
  if (candidate.startsWith('//')) return false
  if (candidate.includes('\\')) return false
  if (candidate.includes('\n') || candidate.includes('\r')) return false
  const path = candidate.split('?')[0] ?? ''
  if (path === '/' || path === '/product' || path === '/about') return false
  return INTERNAL_PATH_PREFIXES.some((prefix) => path === prefix || path.startsWith(prefix + '/'))
}

export function resolveRedirect(redirectTo: string | undefined, next: string | null): string {
  if (isAllowedInternalPath(redirectTo)) return redirectTo as string
  if (isAllowedInternalPath(next)) return next as string
  return '/dashboard'
}

async function postJson(path: string, body: unknown): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch {
    throw new AuthApiError('无法连接服务器，请稍后再试。')
  }
  try {
    return await response.json()
  } catch {
    throw new AuthApiError('服务器返回了无法识别的响应。')
  }
}

export async function login(username: string, password: string, next: string | null): Promise<LoginResponse> {
  const payload = await postJson('/api/login', { username, password, next })
  const parsed = loginResponseSchema.safeParse(payload)
  if (!parsed.success) throw new AuthApiError('登录响应格式不正确。')
  if (!parsed.data.success) {
    throw new AuthApiError(parsed.data.error ?? '登录失败，请检查用户名和密码。')
  }
  return parsed.data
}

export async function register(username: string, password: string): Promise<void> {
  const payload = await postJson('/api/register', { username, password })
  const parsed = simpleResponseSchema.safeParse(payload)
  if (!parsed.success) throw new AuthApiError('注册响应格式不正确。')
  if (!parsed.data.success) {
    throw new AuthApiError(parsed.data.error ?? '注册失败，请稍后再试。')
  }
}

export async function checkAuth(): Promise<{ isLoggedIn: boolean; username: string | null; role: string | null }> {
  let response: Response
  try {
    response = await fetch('/api/check_auth', { headers: { Accept: 'application/json' } })
  } catch {
    return { isLoggedIn: false, username: null, role: null }
  }
  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    return { isLoggedIn: false, username: null, role: null }
  }
  const parsed = z
    .object({
      isLoggedIn: z.boolean(),
      username: z.string().optional(),
      role: z.string().optional(),
    })
    .safeParse(payload)
  if (!parsed.success || !parsed.data.isLoggedIn) {
    return { isLoggedIn: false, username: null, role: null }
  }
  return {
    isLoggedIn: true,
    username: parsed.data.username ?? null,
    role: parsed.data.role ?? null,
  }
}
