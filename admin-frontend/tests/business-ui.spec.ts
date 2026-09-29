import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../src/App.vue'
import SensitiveContentDialog from '../src/components/SensitiveContentDialog.vue'

const { loadIdentityMock } = vi.hoisted(() => ({
  loadIdentityMock: vi.fn(async () => ({
    isLoggedIn: true,
    username: 'admin',
    role: 'admin',
    csrf_token: 'csrf',
  })),
}))

vi.mock('../src/api', async (importOriginal) => {
  const original = await importOriginal<typeof import('../src/api')>()
  return {
    ...original,
    loadIdentity: loadIdentityMock,
    adminApi: {
      ...original.adminApi,
      logout: vi.fn(),
    },
  }
})

const elementStubs = {
  CaButton: {
    props: ['href', 'disabled', 'loading'],
    template: '<component :is="href ? \'a\' : \'button\'" :href="href"><slot /></component>',
  },
  CaEmptyState: { template: '<div class="empty-stub"><slot /></div>' },
  CaLoadingState: { template: '<div class="skeleton-stub" />' },
  ElAlert: { template: '<div class="alert-stub"><slot /></div>' },
  ElButton: { template: '<button><slot /></button>' },
  ElDialog: {
    props: ['modelValue', 'title'],
    emits: ['update:modelValue'],
    template: '<section v-if="modelValue" class="dialog-stub"><slot /><slot name="footer" /></section>',
  },
  ElTooltip: { template: '<span class="tooltip-stub"><slot /></span>' },
}

describe('3.1 管理员界面交互边界', () => {
  beforeEach(() => {
    window.localStorage.clear()
    loadIdentityMock.mockClear()
  })

  it('敏感正文在对话框打开前不请求，打开后只以文本节点展示', async () => {
    const loadChunk = vi.fn(async () => ({
      content: '<img src=x onerror=alert(1)>',
      offset: 0,
      limit: 65536,
      total_length: 32,
      complete: true,
      next_offset: null,
    }))
    const wrapper = mount(SensitiveContentDialog, {
      props: {
        modelValue: false,
        title: '消息正文',
        loadChunk,
        'onUpdate:modelValue': () => undefined,
      },
      global: { stubs: elementStubs },
    })

    expect(loadChunk).not.toHaveBeenCalled()
    await wrapper.setProps({ modelValue: true })
    await flushPromises()

    expect(loadChunk).toHaveBeenCalledOnce()
    expect(wrapper.find('.sensitive-notice').exists()).toBe(true)
    expect(wrapper.find('pre').text()).toBe('<img src=x onerror=alert(1)>')
    expect(wrapper.find('img').exists()).toBe(false)
  })

  it('敏感正文弹窗允许由上层页面承载审计提示', async () => {
    const wrapper = mount(SensitiveContentDialog, {
      props: {
        modelValue: true,
        title: '消息正文',
        loadChunk: vi.fn(async () => ({
          content: '正文',
          offset: 0,
          limit: 65536,
          total_length: 6,
          complete: true,
          next_offset: null,
        })),
        showAuditNotice: false,
        'onUpdate:modelValue': () => undefined,
      },
      global: { stubs: elementStubs },
    })
    await flushPromises()

    expect(wrapper.find('.sensitive-notice').exists()).toBe(false)
  })

  it('桌面侧栏在 264/80 模式间切换并持久化，Logo 始终复用受保护原图', async () => {
    const router = createRouter({
      history: createMemoryHistory('/admin/'),
      routes: [{ path: '/database', component: { template: '<div>database</div>' } }],
    })
    await router.push('/database')
    await router.isReady()
    const wrapper = mount(App, {
      global: {
        plugins: [router],
        stubs: elementStubs,
      },
    })
    await flushPromises()

    expect(wrapper.classes()).not.toContain('sidebar-collapsed')
    expect(wrapper.findAll('img[src="/api/admin/brand/logo"]')).toHaveLength(2)
    expect(wrapper.findAll('.nav-icon svg')).toHaveLength(8)
    expect(wrapper.findAll('.nav-icon').every(icon => icon.text() === '')).toBe(true)
    expect(wrapper.findAll('.nav-icon svg').every(icon => icon.attributes('stroke-width') === '1.5'))
      .toBe(true)
    expect(wrapper.find('.grafana-entry-button').attributes('href'))
      .toBe('http://127.0.0.1:3000/')
    expect(wrapper.find('.grafana-entry-button').text()).toContain('进入 Grafana')
    expect(wrapper.find('.sidebar-toggle svg').exists()).toBe(true)
    await wrapper.find('.sidebar-toggle').trigger('click')
    expect(wrapper.classes()).toContain('sidebar-collapsed')
    expect(window.localStorage.getItem('causalagent.admin.sidebar.collapsed')).toBe('true')

    wrapper.unmount()
    const restored = mount(App, {
      global: {
        plugins: [router],
        stubs: elementStubs,
      },
    })
    await flushPromises()
    expect(restored.classes()).toContain('sidebar-collapsed')
  })

  it('移动端导航可以打开并通过遮罩关闭', async () => {
    const router = createRouter({
      history: createMemoryHistory('/admin/'),
      routes: [{ path: '/database', component: { template: '<div>database</div>' } }],
    })
    await router.push('/database')
    await router.isReady()
    const wrapper = mount(App, {
      global: {
        plugins: [router],
        stubs: elementStubs,
      },
    })
    await flushPromises()

    await wrapper.find('.mobile-menu-button').trigger('click')
    expect(wrapper.find('.admin-sidebar').classes()).toContain('mobile-open')
    await wrapper.find('.sidebar-backdrop').trigger('click')
    expect(wrapper.find('.admin-sidebar').classes()).not.toContain('mobile-open')
  })
})
