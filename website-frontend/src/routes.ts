/* 官网页面路由：Flask 为每个公开路径返回同一份构建产物，这里只按 pathname 选择页面。 */

export type SitePageName =
  | 'home'
  | 'product'
  | 'pricing'
  | 'about'
  | 'docs'
  | 'changelog'
  | 'sign-in'
  | 'sign-up'
  | 'not-found'

export interface SiteRoute {
  name: SitePageName
  path: string
}

/* 每个公开页面的标题与说明取自官网原型各自的 head。 */
export interface SitePageMeta {
  title: string
  description: string
}

export const SITE_PAGE_META: Readonly<Record<SitePageName, SitePageMeta>> = {
  home: {
    title: 'CausalAgent：用因果连接世界',
    description: 'CausalAgent 用因果连接世界：上传你想分析的内容，它会读懂材料，结合知识库选对因果方法，给出可以复核的结论。',
  },
  product: {
    title: '产品矩阵 · CausalAgent 与 DMIRLAB',
    description: 'CausalAgent 应用、CDFM 因果发现基础模型与 DMIR 算法库三件产品的总览：各自解决什么问题、入口在哪、论文与源码在哪。',
  },
  pricing: {
    title: '价格 · CausalAgent',
    description: 'CausalAgent 价格页面：查看 FREE 与 FUTURE 两个方案。',
  },
  about: {
    title: '关于 CausalAgent',
    description: '了解 CausalAgent 对因果关系、证据与可复核分析的理解，并与我们取得联系。',
  },
  docs: {
    title: '使用文档 · CausalAgent',
    description: 'CausalAgent 使用文档：入口与权限、工作区、材料与文件、任务执行、结果与报告、算法说明、账号与常见问题。',
  },
  changelog: {
    title: '更新日志 · CausalAgent',
    description: 'CausalAgent 的公开更新日志：只记录影响使用方式的变化，按版本列出重点变化与具体细节。',
  },
  'sign-in': {
    title: '登录 · CausalAgent',
    description: '登录 CausalAgent，回到工作区继续查看历史会话、数据文件和已经留下的分析过程。',
  },
  'sign-up': {
    title: '注册 · CausalAgent',
    description: '注册 CausalAgent 账号，免费上传数据、运行分析任务并导出报告。',
  },
  'not-found': {
    title: '页面不存在 · CausalAgent',
    description: '该地址没有对应的公开页面。',
  },
}

/* 认证页面不在官网主导航中，但仍属于官网构建产物。 */
const PAGE_BY_PATH: Readonly<Record<string, SitePageName>> = {
  '/': 'home',
  '/product': 'product',
  '/pricing': 'pricing',
  '/about': 'about',
  '/docs': 'docs',
  '/changelog': 'changelog',
  '/auth/sign-in': 'sign-in',
  '/auth/sign-up': 'sign-up',
}

export function normalizePath(pathname: string): string {
  if (!pathname) return '/'
  const trimmed = pathname.replace(/\/+$/, '')
  if (trimmed === '') return '/'
  return pathname.endsWith('/') ? trimmed : pathname
}

export function readSiteRoute(pathname: string): SiteRoute {
  const path = normalizePath(pathname)
  return { name: PAGE_BY_PATH[path] ?? 'not-found', path }
}

export function readQueryValue(search: string, key: string): string | null {
  const params = new URLSearchParams(search)
  const value = params.get(key)
  return value && value.trim() ? value : null
}

