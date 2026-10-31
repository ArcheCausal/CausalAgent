import { effectRequestSchema, effectExecutionContextSchema } from '../../src/api/effect-request.schemas'
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
          measurement_columns: { device: 'device_id', raw_alarm: null },
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
        measurementColumns: { device: 'device_id', raw_alarm: null },
        limitations: ['不包含未触发告警的设备日期'],
      },
    })
  })
})


const fixed = { effect: 'average_total_effect', intervention: { type: 'fixed_contrast', from: 5, to: 7, unit: 'L/min' } }
const shift = { effect: 'average_total_effect', intervention: { type: 'additive_shift', delta: 1, unit: 'L/min' } }
const context = {
  treatment_column: 'flow', outcome_column: 'yield', treatment_unit: 'L/min',
  treatment_transform: { type: 'identity', source_column: 'flow', output_column: 'flow', source_unit: 'L/min', output_unit: 'L/min' },
  outcome_transform: { type: 'identity' },
}

function interaction(extra: Record<string, unknown>) {
  return {
    type: 'interrupt', question_id: 'q-1', message: '请确认研究定义。',
    interaction: {
      type: 'interaction', interaction_type: 'data_processing_plan_confirmation', question_id: 'q-1',
      message: '请确认研究定义。', plan_version: 'a'.repeat(64), allow_other: true,
      plan: { summary: '保留原值', observation_unit: '一次实验', transformations: ['保留原值'],
        variable_semantics: { flow: '流量', yield: '产量' }, role_columns: { treatment: 'flow', target: 'yield' },
        limitations: [], ...extra },
    },
  }
}

describe('confirmed effect request projection', () => {
  it.each([fixed, shift, { ...fixed, target_population: '设备 A 的观测' }, null])('preserves explicit request %j through SSE parsing and recovery', (effect) => {
    const event = interruptEventSchema.parse(interaction({ effect_request: effect, effect_execution_context: context }))
    const waiting = waitingInputFromInteraction(event, event.question_id, event.message)
    expect(waiting?.plan?.effectRequest).toEqual(effect)
    expect(waiting?.plan?.effectExecutionContext).toEqual(context)
    const restored = waitingInputFromInteraction(JSON.parse(JSON.stringify(event)), event.question_id, event.message)
    expect(restored).toEqual(waiting)
  })

  it('keeps a missing historical field distinct from explicit null', () => {
    const legacy = waitingInputFromInteraction(interaction({}), 'q-1', '请确认')
    const current = waitingInputFromInteraction(interaction({ effect_request: null }), 'q-1', '请确认')
    expect(legacy?.plan?.effectRequest).toBeUndefined()
    expect(current?.plan?.effectRequest).toBeNull()
  })

  it.each([
    { ...fixed, internal: 'private' },
    { effect: 'average_total_effect', intervention: { type: 'fixed_contrast', unit: 'L/min' } },
    { ...fixed, intervention: { ...fixed.intervention, from: '5' } },
    { ...fixed, intervention: { ...fixed.intervention, from: true } },
    { ...fixed, intervention: { ...fixed.intervention, from: Infinity } },
    { ...fixed, intervention: { ...fixed.intervention, unit: ' ' } },
    { ...shift, intervention: { ...shift.intervention, from: 5 } },
  ])('rejects invalid definitions without substituting null', (request) => {
    expect(effectRequestSchema.safeParse(request).success).toBe(false)
    expect(interruptEventSchema.safeParse(interaction({ effect_request: request })).success).toBe(false)
    expect(waitingInputFromInteraction(interaction({ effect_request: request }), 'q-1', '确认')?.plan).toBeNull()
  })

  it('retains confirmed affine coefficients and business units', () => {
    const affine = { ...context, treatment_column: 'flow_z', treatment_transform: {
      ...context.treatment_transform, type: 'affine', output_column: 'flow_z', output_unit: '无量纲', scale: 0.5, offset: -3,
    } }
    expect(effectExecutionContextSchema.parse(affine)).toEqual(affine)
    expect(waitingInputFromInteraction(interaction({ effect_request: fixed, effect_execution_context: affine }), 'q-1', '确认')?.plan?.effectRequest).toEqual(fixed)
  })

  it('does not replace unknown transforms with identity', () => {
    const unknown = { ...context, treatment_transform: null, outcome_transform: null }
    expect(effectExecutionContextSchema.parse(unknown)).toEqual(unknown)
    const invalid = { ...context, treatment_transform: { ...context.treatment_transform, output_unit: 'standardized' } }
    expect(effectExecutionContextSchema.safeParse(invalid).success).toBe(false)
  })
})
