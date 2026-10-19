import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ProcessingOptions from '../../src/components/ProcessingOptions.vue'
import type { WaitingInput } from '../../src/types/domain'

function waiting(overrides: Partial<WaitingInput> = {}): WaitingInput {
  return {
    questionId: 'q-1',
    prompt: '请选择处理方式',
    expiresAt: new Date(Date.now() + 60_000).toISOString(),
    allowOther: true,
    selectedOptionId: null,
    expired: false,
    options: [
      {
        optionId: 'opt_abc',
        label: '删除缺失行',
        capabilityId: 'causal.pc',
        steps: [{ operation: 'drop_rows', columns: ['age'], method: null }],
        impact: ['剩余行数：未计算'],
        risks: ['样本量会减少'],
      },
    ],
    ...overrides,
  }
}

describe('ProcessingOptions', () => {
  it('emits the chosen option and shows impact text', async () => {
    const wrapper = mount(ProcessingOptions, { props: { waiting: waiting() } })
    expect(wrapper.text()).toContain('剩余行数：未计算')
    expect(wrapper.text()).toContain('样本量会减少')

    await wrapper.get('.processing-option-button').trigger('click')
    expect(wrapper.emitted('choose')).toEqual([['opt_abc']])
  })

  it('disables selection after the deadline and explains why', async () => {
    const wrapper = mount(ProcessingOptions, {
      props: { waiting: waiting({ expiresAt: new Date(Date.now() - 1_000).toISOString() }) },
    })
    expect(wrapper.text()).toContain('已过期')
    expect(wrapper.get('.processing-option-button').attributes('disabled')).toBeDefined()
  })

  it('keeps the other requirement entry when allowed', async () => {
    const wrapper = mount(ProcessingOptions, { props: { waiting: waiting() } })
    await wrapper.get('.processing-option-other').trigger('click')
    expect(wrapper.emitted('other')).toHaveLength(1)
  })
})
