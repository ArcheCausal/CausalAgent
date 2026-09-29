import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import StatusCard from '../src/components/StatusCard.vue'

describe('StatusCard', () => {
  it('把过期 healthy 快照渲染为警告并保留来源提示', () => {
    const wrapper = mount(StatusCard, {
      props: {
        label: '连接使用率',
        value: '12%',
        detail: '12 / 100 连接',
        meta: {
          status: 'healthy',
          source_alias: 'primary',
          observed_at: '2026-07-25T00:00:00Z',
          is_stale: true,
        },
      },
      global: {
        stubs: {
          CaCard: { template: '<article><slot /></article>' },
          CaBadge: {
            props: ['tone'],
            template: '<span class="badge-stub" :data-tone="tone"><slot /></span>',
          },
        },
      },
    })

    expect(wrapper.classes()).toContain('status-warning')
    expect(wrapper.text()).toContain('连接使用率')
    expect(wrapper.text()).toContain('警告')
    expect(wrapper.text()).toContain('primary')
    expect(wrapper.text()).toContain('已过期')
    expect(wrapper.get('.badge-stub').attributes('data-tone')).toBe('neutral')
    expect(wrapper.get('.card-meta').attributes('title')).toContain('primary')
    expect(wrapper.get('.card-meta').attributes('title')).toContain('2026')
  })
})
