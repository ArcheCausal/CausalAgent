import { describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type { DecodedSseEvent } from '../../src/api/events.schemas'
import { useJobsStore } from '../../src/stores/jobs.store'
import { createJobRecord, reduceJobEvent, settleDecision } from '../../src/runtime/jobs/event-reducer'

function event(type: string, id: number, fields: Record<string, unknown> = {}, known = true): DecodedSseEvent {
  return { event: type, id, data: { type, ...fields }, known }
}

describe('job reducer and cursor semantics', () => {
  it('advances transport/resume cursor for unknown legal events without visible projection', () => {
    let state = createJobRecord('job-1', 'session-1', 'running')
    const unknown = reduceJobEvent(state, event('future_event', 1, { value: 'kept for transport only' }, false))
    state = unknown.state

    expect(unknown).toMatchObject({ applied: false, unknown: true, duplicate: false })
    expect(state.transportCursor).toBe(1)
    expect(state.resumeEventId).toBe(1)
    expect(state.renderedEventId).toBe(0)
    expect(state.events).toHaveLength(0)
  })

  it('deduplicates repeated IDs and text_delta semantic keys independently', () => {
    let state = createJobRecord('job-1', 'session-1', 'running')
    state = reduceJobEvent(state, event('text_delta', 1, { step_id: 's1', stream_id: 'answer', sequence: 1, delta: 'A' })).state
    const repeatedText = reduceJobEvent(state, event('text_delta', 2, { step_id: 's1', stream_id: 'answer', sequence: 1, delta: 'A-again' }))
    state = repeatedText.state
    const duplicateId = reduceJobEvent(state, event('text_delta', 2, { step_id: 's1', stream_id: 'answer', sequence: 2, delta: 'B' }))

    expect(repeatedText.applied).toBe(false)
    expect(state.transportCursor).toBe(2)
    expect(state.renderedEventId).toBe(1)
    expect(state.textStreams.answer).toBe('A')
    expect(duplicateId.duplicate).toBe(true)
    expect(duplicateId.state.textStreams.answer).toBe('A')
  })

  it('corrects an existing draft with the final text and releases raw text buffers', () => {
    let state = createJobRecord('job-1', 'session-1', 'running')
    state = reduceJobEvent(state, event('text_delta', 1, { step_id: 's1', stream_id: 'answer', sequence: 1, delta: 'partial' })).state
    const result = reduceJobEvent(state, event('final_result', 2, { data: { type: 'text', summary: 'done' } }))

    expect(result.terminal).toBe(true)
    expect(result.state.thinking.draftText).toBe('done')
    expect(result.state.thinking.finalResult).toBeNull()
    expect(result.state.textSeenKeys).toEqual([])
    expect(result.state.textStreams).toEqual({})
  })

  it('corrects a report draft in place instead of rendering it twice', () => {
    let state = createJobRecord('job-1', 'session-1', 'running')
    state = reduceJobEvent(state, event('text_delta', 1, { step_id: 's1', stream_id: 'answer', sequence: 1, delta: 'partial' })).state
    const result = reduceJobEvent(state, event('final_result', 2, { data: { type: 'text', layout: 'report', summary: '报告正文' } }))

    expect(result.state.thinking.draftText).toBe('报告正文')
    expect(result.state.thinking.finalResult).toBeNull()
  })

  it('keeps a final result without a draft, and keeps a graph result separate', () => {
    let state = createJobRecord('job-1', 'session-1', 'running')
    const plain = reduceJobEvent(state, event('final_result', 1, { data: { type: 'text', summary: 'done' } }))
    expect(plain.state.thinking.finalResult).toMatchObject({ type: 'text', summary: 'done' })
    expect(plain.state.thinking.draftText).toBe('')

    state = createJobRecord('job-1', 'session-1', 'running')
    state = reduceJobEvent(state, event('text_delta', 1, { step_id: 's1', stream_id: 'answer', sequence: 1, delta: 'partial' })).state
    const graph = reduceJobEvent(state, event('final_result', 2, { data: { type: 'causal_graph', data: { nodes: [], edges: [] } } }))
    expect(graph.state.thinking.draftText).toBe('')
    expect(graph.state.thinking.finalResult).toMatchObject({ type: 'causal_graph' })
  })

  it('streams a public decision into its own step and completes it once', () => {
    let state = createJobRecord('job-1', 'session-1', 'running')
    state = reduceJobEvent(state, event('node_start', 1, { step_id: 's1', node_name: 'deep_agent', title: 'Deep Agent' })).state
    state = reduceJobEvent(state, event('decision_delta', 2, {
      step_id: 's1', node_name: 'deep_agent', title: 'Deep Agent',
      stream_id: 'decision-1', sequence: 1, delta: '选择 PC', decision_kind: 'algorithm', tool_name: 'pc',
    })).state
    state = reduceJobEvent(state, event('decision_delta', 3, {
      step_id: 's1', node_name: 'deep_agent', title: 'Deep Agent',
      stream_id: 'decision-1', sequence: 2, delta: '，因为样本充足', decision_kind: 'algorithm', tool_name: 'pc',
    })).state

    expect(state.thinking.draftText).toBe('')
    expect(state.thinking.steps.s1?.details ?? []).toEqual([{
      kind: 'decision',
      key: 's1:algorithm:pc',
      decisionKind: 'algorithm',
      toolName: 'pc',
      streamId: 'decision-1',
      text: '选择 PC，因为样本充足',
      complete: false,
      released: false,
      pending: [],
    }])

    const completed = reduceJobEvent(state, event('decision', 4, {
      step_id: 's1', node_name: 'deep_agent', title: 'Deep Agent',
      summary: '选择 PC，因为样本充足', decision_kind: 'algorithm', tool_name: 'pc',
    }))
    const details = completed.state.thinking.steps.s1?.details ?? []
    expect(details).toHaveLength(1)
    expect(details[0]).toMatchObject({ kind: 'decision', complete: true })
  })

  it('holds tool lifecycle events until the decision text is fully revealed, then releases them in order', () => {
    let state = createJobRecord('job-1', 'session-1', 'running')
    state = reduceJobEvent(state, event('node_start', 1, { step_id: 's1', node_name: 'deep_agent', title: 'Deep Agent' })).state
    state = reduceJobEvent(state, event('decision_delta', 2, {
      step_id: 's1', node_name: 'deep_agent', title: 'Deep Agent',
      stream_id: 'decision-1', sequence: 1, delta: '选择 PC', decision_kind: 'algorithm', tool_name: 'pc',
    })).state
    state = reduceJobEvent(state, event('tool_call_start', 3, {
      step_id: 's1', node_name: 'deep_agent', title: 'Deep Agent',
      tool_name: 'pc', argument_keys: ['data'],
    })).state
    expect(state.thinking.steps.s1?.details ?? []).toHaveLength(1)

    state = reduceJobEvent(state, event('decision', 4, {
      step_id: 's1', node_name: 'deep_agent', title: 'Deep Agent',
      summary: '选择 PC', decision_kind: 'algorithm', tool_name: 'pc',
    })).state
    expect(state.thinking.steps.s1?.details ?? []).toHaveLength(1)
    expect(state.thinking.steps.s1?.details[0]).toMatchObject({ kind: 'decision', complete: true, released: false })

    state = settleDecision(state, 's1', 's1:algorithm:pc')
    const labels = (state.thinking.steps.s1?.details ?? []).map((detail) => (
      detail.kind === 'text' ? detail.text : `decision:${detail.key}`
    ))
    expect(labels).toEqual(['decision:s1:algorithm:pc', '调用工具：pc（参数字段：data）'])

    const repeated = settleDecision(state, 's1', 's1:algorithm:pc')
    expect(repeated.thinking.steps.s1?.details ?? []).toHaveLength(2)
  })

  it('flushes unsettled decisions and their held tool events on a terminal result', () => {
    let state = createJobRecord('job-1', 'session-1', 'running')
    state = reduceJobEvent(state, event('node_start', 1, { step_id: 's1', node_name: 'deep_agent', title: 'Deep Agent' })).state
    state = reduceJobEvent(state, event('decision_delta', 2, {
      step_id: 's1', node_name: 'deep_agent', title: 'Deep Agent',
      stream_id: 'decision-1', sequence: 1, delta: '选择 PC', decision_kind: 'algorithm', tool_name: 'pc',
    })).state
    state = reduceJobEvent(state, event('tool_call_start', 3, {
      step_id: 's1', node_name: 'deep_agent', title: 'Deep Agent',
      tool_name: 'pc', argument_keys: ['data'],
    })).state

    const finished = reduceJobEvent(state, event('final_result', 4, { data: { type: 'text', summary: 'done' } }))
    const details = finished.state.thinking.steps.s1?.details ?? []
    expect(details[0]).toMatchObject({ kind: 'decision', released: true })
    expect(details.map((detail) => (detail.kind === 'text' ? detail.text : 'decision'))).toEqual([
      'decision',
      '调用工具：pc（参数字段：data）',
    ])
  })

  it('releases a held tool result when no full decision follows', () => {
    let state = createJobRecord('job-1', 'session-1', 'running')
    state = reduceJobEvent(state, event('node_start', 1, { step_id: 's1', node_name: 'deep_agent', title: 'Deep Agent' })).state
    state = reduceJobEvent(state, event('decision_delta', 2, {
      step_id: 's1', node_name: 'deep_agent', title: 'Deep Agent',
      stream_id: 'decision-1', sequence: 1, delta: '检索网页', decision_kind: 'evidence', tool_name: 'web_evidence_search',
    })).state
    state = reduceJobEvent(state, event('tool_call_start', 3, {
      step_id: 's1', node_name: 'deep_agent', title: 'Deep Agent',
      tool_name: 'web_evidence_search', argument_keys: ['query'],
    })).state
    const flushed = reduceJobEvent(state, event('tool_call_result', 4, {
      step_id: 's1', node_name: 'deep_agent', title: 'Deep Agent',
      tool_name: 'web_evidence_search', summary: '返回 3 条来源',
    }))

    const labels = (flushed.state.thinking.steps.s1?.details ?? []).map((detail) => (
      detail.kind === 'text' ? detail.text : 'decision'
    ))
    expect(labels).toEqual(['decision', '调用工具：web_evidence_search（参数字段：query）', 'web_evidence_search：返回 3 条来源'])
  })

  it('defers step details that arrive before their parent stage and replays them once', () => {
    let state = createJobRecord('job-1', 'session-1', 'running')
    state = reduceJobEvent(state, event('tool_call_start', 1, {
      step_id: 's1', node_name: 'deep_agent', title: 'Deep Agent',
      tool_name: 'pc', argument_keys: ['data'],
    })).state

    expect(state.thinking.stepOrder).toEqual([])
    expect(state.thinking.pendingStepEvents.s1).toHaveLength(1)

    state = reduceJobEvent(state, event('node_start', 2, { step_id: 's1', node_name: 'deep_agent', title: 'Deep Agent' })).state
    expect(state.thinking.stepOrder).toEqual(['s1'])
    expect(state.thinking.steps.s1?.details ?? []).toEqual([
      { kind: 'text', text: '调用工具：pc（参数字段：data）', tone: 'default' },
    ])
    expect(state.thinking.pendingStepEvents.s1).toBeUndefined()
  })

  it('renders a historical full decision as prefixed text and records retries with tones', () => {
    let state = createJobRecord('job-1', 'session-1', 'running')
    state = reduceJobEvent(state, event('node_start', 1, { step_id: 's1', node_name: 'deep_agent', title: 'Deep Agent' })).state
    state = reduceJobEvent(state, event('decision', 2, {
      step_id: 's1', node_name: 'deep_agent', title: 'Deep Agent',
      summary: '检索到 3 条来源', decision_kind: 'evidence',
    })).state
    state = reduceJobEvent(state, event('text_delta', 3, { step_id: 's1', stream_id: 'answer', sequence: 1, delta: 'partial' })).state
    const retried = reduceJobEvent(state, event('node_retry', 4, {
      step_id: 's1', node_name: 'deep_agent', title: 'Deep Agent',
      message: '连接超时', discard_stream_id: 'answer',
    }))

    expect(retried.state.thinking.draftText).toBe('')
    expect(retried.state.thinking.draftStreamId).toBeNull()
    const details = retried.state.thinking.steps.s1?.details ?? []
    expect(details[0]).toEqual({ kind: 'text', text: '检索决策：检索到 3 条来源', tone: 'default' })
    expect(details.map((detail) => (detail.kind === 'text' ? detail.tone : 'decision'))).toEqual(['default', 'error', 'retry'])
  })

  it('does not mutate a completed job with a later known event', () => {
    let state = createJobRecord('job-1', 'session-1', 'running')
    state = reduceJobEvent(state, event('final_result', 1, { data: { type: 'text', summary: 'done' } })).state
    const lateEvent = reduceJobEvent(state, event('progress', 2, { step_id: 'late', node_name: 'late', title: 'late', summary: 'ignored' }))

    expect(lateEvent.applied).toBe(false)
    expect(lateEvent.state.transportCursor).toBe(2)
    expect(lateEvent.state.thinking.stepOrder).toEqual([])
    expect(lateEvent.state.thinking.finalResult).toMatchObject({ summary: 'done' })
  })

  it('keeps an active endpoint observation separate from the rendered history cursor', () => {
    setActivePinia(createPinia())
    const jobs = useJobsStore()
    jobs.startFromResponse('job-1', 'session-1', 'running', 5)
    jobs.observeActive({ job_id: 'job-1', session_id: 'session-1', status: 'running', last_event_id: 2 })

    expect(jobs.get('job-1')).toMatchObject({
      transportCursor: 5,
      resumeEventId: 5,
      renderedEventId: 5,
      observedLastEventId: 2,
    })
  })
})
