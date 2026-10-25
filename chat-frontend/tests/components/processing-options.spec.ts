import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ProcessingOptions from '../../src/components/ProcessingOptions.vue'
import type { WaitingInput } from '../../src/types/domain'

function waiting(overrides: Partial<WaitingInput> = {}): WaitingInput {
  return {
    questionId: 'q-1',
    interactionType: 'data_processing_request',
    prompt: '请选择处理方式',
    algorithmName: 'PC 因果发现',
    expiresAt: new Date(Date.now() + 60_000).toISOString(),
    allowOther: true,
    selectedOptionId: null,
    expired: false,
    planVersion: null,
    plan: null,
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
    expect(wrapper.text()).toContain('删除“age”中含缺失值的行')
    expect(wrapper.text()).toContain('PC 因果发现')
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

  it('shows a text-answer entry for an open data question with no options', async () => {
    const wrapper = mount(ProcessingOptions, {
      props: {
        waiting: waiting({
          interactionType: 'data_processing_question',
          prompt: '每一行代表什么观察单位？',
          options: [],
        }),
      },
    })

    expect(wrapper.attributes('aria-label')).toBe('数据问题回答')
    expect(wrapper.text()).toContain('请在下方输入框回答这个问题后继续。')
    expect(wrapper.text()).toContain('输入回答')
    expect(wrapper.find('.processing-option-list').exists()).toBe(false)

    await wrapper.get('.processing-option-other').trigger('click')
    expect(wrapper.emitted('other')).toHaveLength(1)
  })

  it('shows the versioned plan and emits explicit confirmation', async () => {
    const wrapper = mount(ProcessingOptions, {
      props: {
        waiting: waiting({
          interactionType: 'data_processing_plan_confirmation',
          options: [],
          planVersion: 'a'.repeat(64),
          plan: {
            summary: '按设备和日期统计告警数。',
            observationUnit: '设备-日期',
            transformations: ['按设备和日期分组并计数'],
            variableSemantics: { alarm_count: '该设备当天的告警次数' },
            limitations: ['不包含未触发告警的设备日期'],
          },
        }),
      },
    })

    expect(wrapper.text()).toContain('按设备和日期统计告警数。')
    expect(wrapper.text()).toContain('设备-日期')
    expect(wrapper.text()).toContain('修改或拒绝方案')
    expect(wrapper.text()).toContain('确认方案并继续')
    await wrapper.get('.processing-plan-confirm').trigger('click')
    expect(wrapper.emitted('choose')).toEqual([['__confirm_processing_plan__']])
  })
})
