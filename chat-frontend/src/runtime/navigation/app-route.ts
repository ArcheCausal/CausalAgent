/* 普通用户应用路径规则：生产由 Flask 在 /dashboard 下提供，Vite 开发服务器在 /dashboard-assets 下提供。 */

export const DASHBOARD_BASE = '/dashboard'
export const DASHBOARD_ASSETS_BASE = '/dashboard-assets'
export const DASHBOARD_SETTINGS_PATH = '/dashboard/settings'
export const SIGN_IN_PATH = '/auth/sign-in'

export type DashboardView = 'workspace' | 'settings'

export interface DashboardRoute {
  view: DashboardView
  sessionId: string | null
  path: string
}

const SESSION_PATH_PATTERN = /^\/dashboard\/session\/([A-Za-z0-9._-]{1,64})$/

function toDashboardPath(pathname: string): string {
  if (!pathname) return DASHBOARD_BASE
  if (pathname === DASHBOARD_BASE || pathname === DASHBOARD_BASE + '/') return DASHBOARD_BASE
  if (pathname === DASHBOARD_ASSETS_BASE || pathname === DASHBOARD_ASSETS_BASE + '/') return DASHBOARD_BASE
  if (pathname.startsWith(DASHBOARD_ASSETS_BASE + '/')) {
    return DASHBOARD_BASE + pathname.slice(DASHBOARD_ASSETS_BASE.length)
  }
  return pathname
}

export function readDashboardRoute(pathname: string): DashboardRoute {
  const path = toDashboardPath(pathname)
  if (path === DASHBOARD_SETTINGS_PATH) {
    return { view: 'settings', sessionId: null, path: DASHBOARD_SETTINGS_PATH }
  }
  const sessionMatch = SESSION_PATH_PATTERN.exec(path)
  const sessionId = sessionMatch?.[1] ?? null
  if (sessionId) {
    return { view: 'workspace', sessionId, path: DASHBOARD_BASE + '/session/' + sessionId }
  }
  return { view: 'workspace', sessionId: null, path: DASHBOARD_BASE }
}

export function dashboardBase(pathname: string): string {
  if (pathname === DASHBOARD_ASSETS_BASE) return DASHBOARD_ASSETS_BASE
  return pathname.startsWith(DASHBOARD_ASSETS_BASE + '/') ? DASHBOARD_ASSETS_BASE : ''
}

export function dashboardHref(routePath: string, pathname: string): string {
  const base = dashboardBase(pathname)
  if (!base) return routePath
  const suffix = routePath.startsWith(DASHBOARD_BASE) ? routePath.slice(DASHBOARD_BASE.length) : ''
  return suffix ? base + suffix : base + '/'
}

export function sessionHref(sessionId: string, pathname: string): string {
  return dashboardHref(DASHBOARD_BASE + '/session/' + sessionId, pathname)
}

export function settingsHref(pathname: string): string {
  return dashboardHref(DASHBOARD_SETTINGS_PATH, pathname)
}

export function workspaceHref(pathname: string): string {
  return dashboardHref(DASHBOARD_BASE, pathname)
}

export function signInUrl(next: string): string {
  return SIGN_IN_PATH + '?next=' + encodeURIComponent(next)
}

