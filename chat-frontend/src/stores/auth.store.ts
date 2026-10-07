import { defineStore } from 'pinia'
import { api } from '../api/client'
import { ApiError } from '../api/errors'
import type { Locale } from '../types/domain'

type AuthPhase = 'checking' | 'anonymous' | 'authenticated'

interface AuthState {
  phase: AuthPhase
  username: string | null
  role: string | null
  permissions: string[]
  csrfToken: string | null
  warningCode: string | null
  error: string | null
}

function responseError(message: string): ApiError {
  return new ApiError({ status: null, code: 'auth_response_error', message, requestId: null })
}

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({
    phase: 'checking',
    username: null,
    role: null,
    permissions: [],
    csrfToken: null,
    warningCode: null,
    error: null,
  }),
  getters: {
    isAuthenticated: (state) => state.phase === 'authenticated',
    hasPermission: (state) => (permissionKey: string) => state.permissions.includes(permissionKey),
    locale: (): Locale => (localStorage.getItem('language') === 'en' ? 'en' : 'zh'),
  },
  actions: {
    async check(): Promise<boolean> {
      this.phase = 'checking'
      try {
        const response = await api.checkAuth()
        if (response.isLoggedIn) {
          this.phase = 'authenticated'
          this.username = response.username
          this.role = response.role ?? 'user'
          this.permissions = response.permissions ?? []
          this.csrfToken = response.csrf_token ?? null
          this.error = null
          return true
        }
        this.clearIdentity()
        return false
      } catch (error) {
        this.clearIdentity()
        this.error = error instanceof Error ? error.message : '无法连接服务器检查登录状态。'
        return false
      }
    },
    async register(username: string, password: string): Promise<void> {
      const response = await api.register(username, password)
      if (!response.success) throw responseError(response.error || '注册失败，请稍后再试。')
    },
    async login(username: string, password: string, next: string | null): Promise<string | null> {
      const response = await api.login(username, password, next)
      if (!response.success || !response.username) {
        this.clearIdentity()
        throw responseError(response.error || '登录失败，请检查用户名和密码。')
      }
      this.phase = 'authenticated'
      this.username = response.username
      this.role = response.role ?? 'user'
      this.permissions = response.permissions ?? []
      this.csrfToken = response.csrf_token ?? null
      this.warningCode = response.warning_code ?? null
      this.error = null
      return response.redirect_to ?? null
    },
    async logout(): Promise<void> {
      await api.logout()
      this.clearIdentity()
    },
    handleUnauthorized(): void {
      this.clearIdentity()
    },
    clearIdentity(): void {
      this.phase = 'anonymous'
      this.username = null
      this.role = null
      this.permissions = []
      this.csrfToken = null
      this.warningCode = null
    },
  },
})
