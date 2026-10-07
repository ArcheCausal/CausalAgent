import { describe, expect, it } from 'vitest'
import {
  DASHBOARD_ASSETS_BASE,
  dashboardHref,
  readDashboardRoute,
  sessionHref,
  settingsHref,
  signInUrl,
  workspaceHref,
} from '../../src/runtime/navigation/app-route'

describe('普通用户应用路由', () => {
  it('把生产路径解析为工作区、设置与会话详情', () => {
    expect(readDashboardRoute('/dashboard')).toEqual({ view: 'workspace', sessionId: null, path: '/dashboard' })
    expect(readDashboardRoute('/dashboard/')).toEqual({ view: 'workspace', sessionId: null, path: '/dashboard' })
    expect(readDashboardRoute('/dashboard/settings')).toEqual({
      view: 'settings',
      sessionId: null,
      path: '/dashboard/settings',
    })
    expect(readDashboardRoute('/dashboard/session/abc-123')).toEqual({
      view: 'workspace',
      sessionId: 'abc-123',
      path: '/dashboard/session/abc-123',
    })
  })

  it('把开发服务器 base 下的路径归一到同一套路由', () => {
    expect(readDashboardRoute(DASHBOARD_ASSETS_BASE + '/')).toEqual({
      view: 'workspace',
      sessionId: null,
      path: '/dashboard',
    })
    expect(readDashboardRoute(DASHBOARD_ASSETS_BASE + '/settings').view).toBe('settings')
    expect(readDashboardRoute(DASHBOARD_ASSETS_BASE + '/session/abc-123').sessionId).toBe('abc-123')
  })

  it('非法会话标识不再被当成会话详情', () => {
    expect(readDashboardRoute('/dashboard/session/' + 'x'.repeat(80))).toEqual({
      view: 'workspace',
      sessionId: null,
      path: '/dashboard',
    })
    expect(readDashboardRoute('/dashboard/session/%2F%2Fevil').sessionId).toBeNull()
  })

  it('未知路径回落到工作区，而不是猜测页面', () => {
    expect(readDashboardRoute('/dashboard/unknown').path).toBe('/dashboard')
    expect(readDashboardRoute('/dashboard-assets/unknown').path).toBe('/dashboard')
  })

  it('按当前页面决定站内链接的 base', () => {
    expect(workspaceHref('/dashboard-assets/')).toBe('/dashboard-assets/')
    expect(settingsHref('/dashboard-assets/')).toBe('/dashboard-assets/settings')
    expect(sessionHref('abc', '/dashboard-assets/')).toBe('/dashboard-assets/session/abc')
    expect(workspaceHref('/dashboard')).toBe('/dashboard')
    expect(settingsHref('/dashboard')).toBe('/dashboard/settings')
    expect(sessionHref('abc', '/dashboard/settings')).toBe('/dashboard/session/abc')
    expect(dashboardHref('/dashboard', '/dashboard-assets/settings')).toBe('/dashboard-assets/')
  })

  it('登录地址只携带站内回跳路径', () => {
    expect(signInUrl('/dashboard')).toBe('/auth/sign-in?next=%2Fdashboard')
    expect(signInUrl('/dashboard/session/abc')).toBe('/auth/sign-in?next=%2Fdashboard%2Fsession%2Fabc')
  })
})

