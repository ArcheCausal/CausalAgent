import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SignInPage from '../../src/pages/SignInPage.vue'

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  globalThis.history.replaceState({}, '', '/auth/sign-in?next=%2Frag-eval')
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('登录页面', () => {
  it('提示管理员权限不足时给出明确说明', () => {
    globalThis.history.replaceState({}, '', '/auth/sign-in?notice=admin_required')
    const wrapper = mount(SignInPage, { props: { loggedInUsername: null } })

    expect(wrapper.get('.form-error').text()).toContain('没有管理员权限')
  })

  it('提交后把 next 一起发给登录接口', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ success: false, error: '密码错误' }, 401))
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = mount(SignInPage, { props: { loggedInUsername: null } })

    await wrapper.get('input[name="username"]').setValue('alice')
    await wrapper.get('input[name="password"]').setValue('secret1')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(JSON.parse(String(init.body))).toEqual({ username: 'alice', password: 'secret1', next: '/rag-eval' })
    expect(wrapper.get('.form-error').text()).toContain('密码错误')
  })

  it('缺少输入时不发起请求', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = mount(SignInPage, { props: { loggedInUsername: null } })

    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(fetchMock).not.toHaveBeenCalled()
    expect(wrapper.get('.form-error').text()).toContain('请填写用户名和密码')
  })
})
