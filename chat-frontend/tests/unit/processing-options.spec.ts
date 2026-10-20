import { describe, expect, it } from 'vitest'
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
})
