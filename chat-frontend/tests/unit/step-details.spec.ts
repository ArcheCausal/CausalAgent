import { describe, expect, it } from 'vitest'
import { decisionKindPrefix, stepDetailText } from '../../src/runtime/jobs/step-details'

describe('step detail projection', () => {
  it('prefixes public decisions by kind and ignores unknown kinds', () => {
    expect(decisionKindPrefix('algorithm')).toBe('算法决策：')
    expect(decisionKindPrefix('evidence')).toBe('检索决策：')
    expect(decisionKindPrefix('final')).toBe('最终决策：')
    expect(decisionKindPrefix('future_kind')).toBe('')
  })

  it('projects lifecycle events into the historical detail text', () => {
    expect(stepDetailText({ type: 'tool_call_start', tool_name: 'pc', argument_keys: ['data', 'alpha'] })).toBe('调用工具：pc（参数字段：data、alpha）')
    expect(stepDetailText({ type: 'tool_call_start', tool_name: 'pc', argument_keys: 'bad' })).toBe('调用工具：pc')
    expect(stepDetailText({ type: 'tool_call_result', tool_name: 'pc', summary: '完成' })).toBe('pc：完成')
    expect(stepDetailText({ type: 'progress', summary: '载入数据' })).toBe('载入数据')
    expect(stepDetailText({ type: 'decision', summary: '选择 PC', decision_kind: 'algorithm' })).toBe('算法决策：选择 PC')
  })
})
