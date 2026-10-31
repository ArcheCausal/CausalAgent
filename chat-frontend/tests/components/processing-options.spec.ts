import { enableAutoUnmount, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import ProcessingOptions from '../../src/components/ProcessingOptions.vue'
import type { InteractionIdentity, WaitingInput } from '../../src/types/domain'

enableAutoUnmount(afterEach)

const identity: InteractionIdentity = { jobId: 'job-1', questionId: 'q-1', planVersion: null }

function waiting(overrides: Partial<WaitingInput> = {}): WaitingInput {
  return {
    status: 'waiting',
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

function planWaiting(overrides: Partial<WaitingInput> = {}): WaitingInput {
  return waiting({
    interactionType: 'data_processing_plan_confirmation',
    options: [],
    planVersion: 'version-1',
    plan: {
      summary: '按设备和日期统计告警数。',
      observationUnit: '设备-日期',
      transformations: ['按设备和日期分组并计数'],
      variableSemantics: { ['device_alarm_count_'.repeat(12)]: '该设备当天的告警次数' },
      limitations: ['不包含未触发告警的设备日期'],
      effectRequest: null,
    },
    ...overrides,
  })
}

const waitForTick = () => new Promise<void>((resolve) => window.setTimeout(resolve, 1_100))

describe('ProcessingOptions', () => {
  it('shows the exact contrast, population and confirmed transformation', () => {
    const current = planWaiting()
    current.plan!.effectRequest = { effect: 'average_total_effect', intervention: { type: 'fixed_contrast', from: 5, to: 7, unit: 'L/min' }, target_population: '设备 A 的观测' }
    current.plan!.effectExecutionContext = {
      treatment_column: 'flow_z', outcome_column: 'yield', treatment_unit: 'L/min',
      treatment_transform: { type: 'affine', source_column: 'flow', output_column: 'flow_z', source_unit: 'L/min', output_unit: '无量纲', scale: 0.5, offset: -3 },
      outcome_transform: { type: 'identity' },
    }
    const wrapper = mount(ProcessingOptions, { props: { waiting: current, identity: { ...identity, planVersion: current.planVersion } } })
    expect(wrapper.text()).toContain('从 5 改为 7 L/min')
    expect(wrapper.text()).toContain('设备 A 的观测')
    expect(wrapper.text()).toContain('输出值 = 0.5 × 原始值 + -3')
    expect(wrapper.text()).toContain('因果识别与干预支持由后续分析检查')
  })

  it('shows a shift and leaves unspecified transformation facts unknown', () => {
    const current = planWaiting()
    current.plan!.effectRequest = { effect: 'average_total_effect', intervention: { type: 'additive_shift', delta: -1, unit: 'L/min' } }
    const wrapper = mount(ProcessingOptions, { props: { waiting: current, identity: { ...identity, planVersion: current.planVersion } } })
    expect(wrapper.text()).toContain('当前处理值增加 -1 L/min')
    expect(wrapper.text()).toContain('后续估计不能直接套用业务单位的干预值')
    expect(wrapper.text()).toContain('需先确认结果变量尺度')
  })

  it('distinguishes explicit no-request from a historical unrecorded request and blocks old confirmation', async () => {
    const current = planWaiting()
    const wrapper = mount(ProcessingOptions, { props: { waiting: current, identity: { ...identity, planVersion: current.planVersion } } })
    expect(wrapper.text()).toContain('未请求，仅进行结构探索')
    expect(wrapper.get('.processing-plan-confirm').attributes('disabled')).toBeUndefined()
    const legacy = planWaiting()
    delete legacy.plan!.effectRequest
    await wrapper.setProps({ waiting: legacy })
    expect(wrapper.text()).toContain('未记录效应请求')
    expect(wrapper.get('.processing-plan-confirm').attributes('disabled')).toBeDefined()
    await wrapper.get('.processing-plan-confirm').trigger('click')
    expect(wrapper.emitted('choose')).toBeUndefined()
  })

  it('emits the chosen option and shows impact text', async () => {
    const wrapper = mount(ProcessingOptions, { props: { waiting: waiting(), identity } })
    expect(wrapper.text()).toContain('删除“age”中含缺失值的行')
    expect(wrapper.text()).toContain('PC 因果发现')
    expect(wrapper.text()).toContain('剩余行数：未计算')
    expect(wrapper.text()).toContain('样本量会减少')

    await wrapper.get('.processing-option-button').trigger('click')
    expect(wrapper.emitted('choose')).toEqual([[{ ...identity, optionId: 'opt_abc' }]])
  })

  it('disables selection after the deadline and explains why', async () => {
    const wrapper = mount(ProcessingOptions, {
      props: { waiting: waiting({ expiresAt: new Date(Date.now() - 1_000).toISOString() }), identity },
    })
    expect(wrapper.text()).toContain('已过期')
    expect(wrapper.find('button').exists()).toBe(false)
    expect(wrapper.find('.processing-options-countdown').exists()).toBe(false)
  })

  it('keeps the other requirement entry when allowed', async () => {
    const wrapper = mount(ProcessingOptions, { props: { waiting: waiting(), identity } })
    await wrapper.get('.processing-option-other').trigger('click')
    expect(wrapper.emitted('other')).toEqual([[identity]])
    expect(wrapper.emitted('other')![0]![0]).not.toBe(identity)
  })

  it('shows a text-answer entry for an open data question with no options', async () => {
    const wrapper = mount(ProcessingOptions, {
      props: {
        identity,
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
        identity: { ...identity, planVersion: 'a'.repeat(64) },
        waiting: waiting({
          interactionType: 'data_processing_plan_confirmation',
          options: [],
          planVersion: 'a'.repeat(64),
          plan: {
            summary: '按设备和日期统计告警数。',
            observationUnit: '设备-日期',
            transformations: ['按设备和日期分组并计数'],
            variableSemantics: { alarm_count: '该设备当天的告警次数' },
            roleColumns: { target: 'alarm_count', treatment: 'coolant_flow_l_min' },
            measurementColumns: { device: 'device_id', raw_alarm: null, date: 'date' },
            limitations: ['不包含未触发告警的设备日期'],
            effectRequest: null,
          },
        }),
      },
    })

    expect(wrapper.text()).toContain('按设备和日期统计告警数。')
    expect(wrapper.text()).toContain('设备-日期')
    expect(wrapper.text()).toContain('结果变量对应列')
    expect(wrapper.text()).toContain('alarm_count')
    expect(wrapper.text()).toContain('处理变量对应列')
    expect(wrapper.text()).toContain('coolant_flow_l_min')
    expect(wrapper.text()).toContain('原始测量列处理')
    expect(wrapper.text()).toContain('device：重命名为 device_id')
    expect(wrapper.text()).toContain('raw_alarm：不传递原始测量类型')
    expect(wrapper.text()).toContain('date：保留')
    expect(wrapper.text()).toContain('修改或拒绝方案')
    expect(wrapper.text()).toContain('确认方案并继续')
    await wrapper.get('.processing-plan-confirm').trigger('click')
    expect(wrapper.emitted('choose')).toEqual([[{ ...identity, planVersion: 'a'.repeat(64), optionId: '__confirm_processing_plan__' }]])
  })

  it.each(['answered', 'expired', 'closed'] as const)('keeps the %s plan summary without controls or countdown', (status) => {
    const wrapper = mount(ProcessingOptions, {
      props: {
        identity: { ...identity, planVersion: 'version-1' },
        waiting: planWaiting({ status, expiresAt: new Date(Date.now() - 1_000).toISOString(), expired: true }),
      },
    })
    expect(wrapper.get('.processing-plan-summary').text()).toContain('按设备和日期统计告警数。')
    expect(wrapper.text()).toContain(status === 'answered' ? '已回答' : status === 'closed' ? '已结束' : '已过期')
    expect(wrapper.find('button').exists()).toBe(false)
    expect(wrapper.find('.processing-options-countdown').exists()).toBe(false)
    if (status !== 'expired') expect(wrapper.text()).not.toContain('已过期')
  })

  it('keeps an answered interaction answered beyond its original deadline', async () => {
    const input = waiting({ expiresAt: new Date(Date.now() + 500).toISOString() })
    const wrapper = mount(ProcessingOptions, { props: { waiting: input, identity } })
    await wrapper.setProps({ waiting: { ...input, status: 'answered' } })
    await waitForTick()
    expect(wrapper.get('.processing-options-status').text()).toBe('已回答')
    expect(wrapper.text()).not.toContain('已过期')
    expect(wrapper.find('button').exists()).toBe(false)
    expect(wrapper.find('.processing-options-countdown').exists()).toBe(false)
  })

  it('restarts real countdowns when the question, version, deadline or lifecycle changes', async () => {
    const wrapper = mount(ProcessingOptions, {
      props: { waiting: waiting({ status: 'answered', expiresAt: null }), identity },
    })
    const next = planWaiting({ questionId: 'q-2', expiresAt: new Date(Date.now() + 20_000).toISOString() })
    await wrapper.setProps({ waiting: next, identity: { ...identity, questionId: 'q-2', planVersion: 'version-1' } })
    const firstCountdown = wrapper.get('.processing-options-countdown').text()
    await waitForTick()
    expect(wrapper.get('.processing-options-countdown').text()).not.toBe(firstCountdown)

    // 只改变版本及原始截止时间，组件实例保持不变。
    const revised = { ...next, planVersion: 'version-2', expiresAt: new Date(Date.now() + 500).toISOString() }
    await wrapper.setProps({ waiting: revised, identity: { ...identity, questionId: 'q-2', planVersion: 'version-2' } })
    await waitForTick()
    expect(wrapper.text()).toContain('已过期')
    expect(wrapper.find('button').exists()).toBe(false)
    expect(wrapper.find('.processing-options-countdown').exists()).toBe(false)
  })

  it('starts a countdown when the same question receives a deadline', async () => {
    const wrapper = mount(ProcessingOptions, { props: { waiting: waiting({ expiresAt: null }), identity } })
    expect(wrapper.find('.processing-options-countdown').exists()).toBe(false)
    await wrapper.setProps({ waiting: waiting({ expiresAt: new Date(Date.now() + 500).toISOString() }) })
    await waitForTick()
    expect(wrapper.text()).toContain('已过期')
    expect(wrapper.find('button').exists()).toBe(false)
  })

  it.each([
    { ...identity, jobId: '' },
    { ...identity, questionId: 'stale-question' },
    { ...identity, planVersion: 'stale-version' },
  ])('blocks controls for mismatched identity %j', async (staleIdentity) => {
    const wrapper = mount(ProcessingOptions, { props: { waiting: waiting(), identity: staleIdentity } })
    for (const button of wrapper.findAll('button')) {
      expect(button.attributes('disabled')).toBeDefined()
      await button.trigger('click')
    }
    expect(wrapper.emitted('choose')).toBeUndefined()
    expect(wrapper.emitted('other')).toBeUndefined()
  })

  it('copies the emitted identity so a later question cannot change an earlier action', async () => {
    const currentIdentity = { ...identity }
    const wrapper = mount(ProcessingOptions, { props: { waiting: waiting(), identity: currentIdentity } })
    await wrapper.get('.processing-option-button').trigger('click')
    const action = wrapper.emitted('choose')![0]![0]
    currentIdentity.questionId = 'q-2'
    await wrapper.setProps({ waiting: waiting({ questionId: 'q-2' }) })
    expect(action).toEqual({ ...identity, optionId: 'opt_abc' })
    await wrapper.get('.processing-option-other').trigger('click')
    expect(wrapper.emitted('other')).toEqual([[{ ...identity, questionId: 'q-2' }]])
  })

  it('shows submission and failure states while preserving the plan and original deadline', async () => {
    const input = planWaiting()
    const wrapper = mount(ProcessingOptions, {
      props: { waiting: input, identity: { ...identity, planVersion: input.planVersion }, submitting: true },
    })
    expect(wrapper.attributes('aria-busy')).toBe('true')
    expect(wrapper.get('.processing-plan-confirm').text()).toBe('正在提交…')
    for (const button of wrapper.findAll('button')) {
      expect(button.attributes('disabled')).toBeDefined()
      await button.trigger('click')
    }
    expect(wrapper.emitted('choose')).toBeUndefined()
    expect(wrapper.emitted('other')).toBeUndefined()
    await wrapper.setProps({ submitting: false, errorMessage: '提交失败，请重试。' })
    expect(wrapper.get('[role=alert]').text()).toBe('提交失败，请重试。')
    expect(wrapper.get('.processing-plan-confirm').attributes('disabled')).toBeUndefined()
    expect(wrapper.props('waiting').expiresAt).toBe(input.expiresAt)
    const children = Array.from(wrapper.element.children)
    expect(children.indexOf(wrapper.get('.processing-options-hint').element)).toBeLessThan(children.indexOf(wrapper.get('.processing-plan-summary').element))
    expect(children.indexOf(wrapper.get('.processing-plan-summary').element)).toBeLessThan(children.indexOf(wrapper.get('.processing-plan-confirm').element))
    expect(children.at(-1)).toBe(wrapper.get('.processing-options-countdown').element)
    await wrapper.get('.processing-plan-confirm').trigger('click')
    expect(wrapper.emitted('choose')).toEqual([[{ ...identity, planVersion: input.planVersion, optionId: '__confirm_processing_plan__' }]])
  })

  it('checks the actual deadline on click between countdown ticks', async () => {
    const wrapper = mount(ProcessingOptions, {
      props: { waiting: waiting({ expiresAt: new Date(Date.now() + 100).toISOString() }), identity },
    })
    const button = wrapper.get('.processing-option-button')
    await new Promise<void>((resolve) => window.setTimeout(resolve, 250))
    await button.trigger('click')
    expect(wrapper.emitted('choose')).toBeUndefined()
    expect(wrapper.text()).toContain('已过期')
    expect(wrapper.find('button').exists()).toBe(false)
  })

  it.each(['serverExpired', 'expired'] as const)('removes controls and countdown when %s is set', async (expirySource) => {
    const input = waiting()
    const wrapper = mount(ProcessingOptions, { props: { waiting: input, identity } })
    await wrapper.setProps(expirySource === 'serverExpired' ? { serverExpired: true } : { waiting: { ...input, expired: true } })
    expect(wrapper.text()).toContain('已过期')
    expect(wrapper.find('button').exists()).toBe(false)
    expect(wrapper.find('.processing-options-countdown').exists()).toBe(false)
  })
})
