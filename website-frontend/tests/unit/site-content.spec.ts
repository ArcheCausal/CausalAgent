import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { SITE_PAGE_META, readSiteRoute } from '../../src/routes'

const frontendRoot = resolve(__dirname, '../..')
const pageSource = (name: string): string =>
  readFileSync(resolve(frontendRoot, 'src/pages', name), 'utf-8')

describe('官网页面元信息', () => {
  it('每个公开页面都有自己的标题与说明', () => {
    const names = [
      'home',
      'product',
      'pricing',
      'about',
      'docs',
      'changelog',
      'sign-in',
      'sign-up',
      'not-found',
    ] as const
    for (const name of names) {
      const meta = SITE_PAGE_META[name]
      expect(meta.title.length).toBeGreaterThan(0)
      expect(meta.description.length).toBeGreaterThan(0)
    }
  })
})

describe('官网页面引用的发布素材', () => {
  it('页面里出现的站内素材都能在 public/ 找到', () => {
    const pages = [
      'HomePage.vue',
      'ProductPage.vue',
      'PricingPage.vue',
      'AboutPage.vue',
      'DocsPage.vue',
      'ChangelogPage.vue',
      'SignInPage.vue',
      'SignUpPage.vue',
    ]
    const missing: string[] = []
    for (const page of pages) {
      const source = pageSource(page)
      for (const match of source.matchAll(/\/site-assets\/([A-Za-z0-9_./\u4e00-\u9fa5-]+)/g)) {
        const relative = decodeURIComponent(match[1] as string)
        if (!existsSync(resolve(frontendRoot, 'public', relative))) missing.push(page + ' -> ' + relative)
      }
    }
    expect(missing).toEqual([])
  })
})

describe('官网公开路径契约', () => {
  it('页面里的站内链接都指向已登记路径或系统入口', () => {
    const systemEntries = ['/dashboard', '/rag-eval', '/admin']
    const pages = [
      'HomePage.vue',
      'ProductPage.vue',
      'PricingPage.vue',
      'AboutPage.vue',
      'DocsPage.vue',
      'ChangelogPage.vue',
      'SignInPage.vue',
      'SignUpPage.vue',
    ]
    const broken: string[] = []
    for (const page of pages) {
      for (const match of pageSource(page).matchAll(/href="(\/[^"#]*)"/g)) {
        const href = match[1] as string
        const path = href.split('?')[0] as string
        /* 站内素材链接指向 public/ 下的发布文件，不属于公开页面路径。 */
        if (path.startsWith('/site-assets/')) continue
        const isSystemEntry = systemEntries.some((prefix) => path === prefix || path.startsWith(prefix + '/'))
        if (isSystemEntry) continue
        if (readSiteRoute(path).name === 'not-found') broken.push(page + ' -> ' + href)
      }
    }
    expect(broken).toEqual([])
  })
})
