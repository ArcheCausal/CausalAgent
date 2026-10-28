import { describe, expect, it } from 'vitest'
import { interruptEventSchema } from '../../src/api/events.schemas'
import { waitingInputFromInteraction } from '../../src/runtime/jobs/event-reducer'

describe('processing option projection', () => {
  it('reads option identifiers, steps and deadline from a public interaction', () => {
    const waiting = waitingInputFromInteraction(
      {
        interaction: {
          type: 'interaction',
          interaction_type: 'data_processing_request',
          question_id: 'q-1',
          message: '请选择缺失值处理方式',
          algorithm_name: 'PC 因果发现',
          expires_at: '2026-10-19T10:00:00.000000Z',
          allow_other: true,
          options: [
            {
              option_id: 'opt_abc',
              label: '删除缺失行',
              capability_id: 'causal.pc',
              steps: [{ operation: 'drop_rows', columns: ['age'], method: null }],
              impact: ['剩余行数：未计算'],
              risks: ['样本量会减少'],
            },
          ],
        },
      },
      'q-1',
      '请选择缺失值处理方式',
    )

    expect(waiting?.questionId).toBe('q-1')
    expect(waiting?.expiresAt).toBe('2026-10-19T10:00:00.000000Z')
    expect(waiting?.interactionType).toBe('data_processing_request')
    expect(waiting?.options).toHaveLength(1)
    expect(waiting?.options[0]).toMatchObject({
      optionId: 'opt_abc',
      capabilityId: 'causal.pc',
    })
    expect(waiting?.options[0]?.steps[0]?.columns).toEqual(['age'])
    expect(waiting?.algorithmName).toBe('PC 因果发现')
  })

  it('keeps plain text interrupts without options usable', () => {
    const waiting = waitingInputFromInteraction({}, 'q-2', '请补充样本说明')
    expect(waiting?.options).toEqual([])
    expect(waiting?.allowOther).toBe(true)
    expect(waiting?.expiresAt).toBeNull()
    expect(waiting?.algorithmName).toBe('')
  })

  it('projects an open data-processing question event without requiring options', () => {
    const event = interruptEventSchema.parse({
      type: 'interrupt',
      question_id: 'q-data-1',
      message: '每一行代表什么观察单位？',
      interaction: {
        type: 'interaction',
        interaction_type: 'data_processing_question',
        question_id: 'q-data-1',
        message: '每一行代表什么观察单位？',
        expires_at: '2026-10-22T12:05:00Z',
        allow_other: true,
      },
    })
    const waiting = waitingInputFromInteraction(event, event.question_id, event.message)

    expect(waiting).toMatchObject({
      questionId: 'q-data-1',
      interactionType: 'data_processing_question',
      prompt: '每一行代表什么观察单位？',
      expiresAt: '2026-10-22T12:05:00Z',
      allowOther: true,
      options: [],
    })
  })

  it('projects a version-bound processing plan for explicit confirmation', () => {
    const version = 'c'.repeat(64)
    const event = interruptEventSchema.parse({
      type: 'interrupt',
      question_id: 'q-plan-1',
      message: '请检查数据处理方案。',
      interaction: {
        type: 'interaction',
        interaction_type: 'data_processing_plan_confirmation',
        question_id: 'q-plan-1',
        message: '请检查数据处理方案。',
        plan_version: version,
        plan: {
          summary: '按设备和日期统计告警数。',
          observation_unit: '设备-日期',
          transformations: ['按设备和日期分组并计数'],
          variable_semantics: { alarm_count: '该设备当天的告警次数' },
          role_columns: { target: 'alarm_count', treatment: null },
          limitations: ['不包含未触发告警的设备日期'],
        },
        allow_other: true,
      },
    })

    const waiting = waitingInputFromInteraction(event, event.question_id, event.message)

    expect(waiting).toMatchObject({
      interactionType: 'data_processing_plan_confirmation',
      planVersion: version,
      plan: {
        summary: '按设备和日期统计告警数。',
        observationUnit: '设备-日期',
        transformations: ['按设备和日期分组并计数'],
        variableSemantics: { alarm_count: '该设备当天的告警次数' },
        roleColumns: { target: 'alarm_count', treatment: null },
        limitations: ['不包含未触发告警的设备日期'],
      },
    })
  })
})
