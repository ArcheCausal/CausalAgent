import { describe, expect, it } from 'vitest'
import { readQueryValue, readSiteRoute } from '../../src/routes'

describe('官网路由解析', () => {
  it('把每个公开路径解析到对应页面', () => {
    const cases: ReadonlyArray<[string, string]> = [
      ['/', 'home'],
      ['/product', 'product'],
      ['/pricing', 'pricing'],
      ['/about', 'about'],
      ['/docs', 'docs'],
      ['/changelog', 'changelog'],
      ['/auth/sign-in', 'sign-in'],
      ['/auth/sign-up', 'sign-up'],
    ]
    for (const [path, name] of cases) {
      expect(readSiteRoute(path).name).toBe(name)
    }
  })

  it('把带尾斜杠的路径归一到同一页面', () => {
    expect(readSiteRoute('/product/').path).toBe('/product')
    expect(readSiteRoute('/product/').name).toBe('product')
    expect(readSiteRoute('/pricing/').name).toBe('pricing')
    expect(readSiteRoute('/auth/sign-in/').name).toBe('sign-in')
  })

  it('未登记的路径返回未找到页面，而不是猜测页面', () => {
    expect(readSiteRoute('/dashboard').name).toBe('not-found')
    expect(readSiteRoute('/admin/database').name).toBe('not-found')
    expect(readSiteRoute('/unknown').name).toBe('not-found')
  })

  it('只读取非空的查询参数', () => {
    expect(readQueryValue('?next=%2Frag-eval', 'next')).toBe('/rag-eval')
    expect(readQueryValue('?next=', 'next')).toBeNull()
    expect(readQueryValue('', 'next')).toBeNull()
  })
})

