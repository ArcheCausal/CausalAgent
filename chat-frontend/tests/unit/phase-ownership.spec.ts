import { describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type { DecodedSseEvent } from '../../src/api/events.schemas'
import { useJobsStore } from '../../src/stores/jobs.store'
import { useSessionsStore } from '../../src/stores/sessions.store'

function event(type: string, id: number, fields: Record<string, unknown> = {}): DecodedSseEvent {
  return { event: type, id, data: { type, ...fields }, known: true }
}

function phase(inputId: number, status: string, events: Array<Record<string, unknown>>) {
  return {
    phaseSequence: inputId,
    status,
    elapsedSeconds: 1,
    lastEventId: events.length ? 100 + events.length : 0,
    analysisJobId: 'job-1',
    analysisJobInputId: inputId,
    events,
  }
}

describe('运行态记录与历史阶段的归属', () => {
  it('同一 Job 只保留最靠后输入的历史阶段', () => {
    setActivePinia(createPinia())
    const jobs = useJobsStore()
    const first = phase(21, 'completed', [{ type: 'node_start', event_id: 1, step_id: 's1', title: '第一次分析' }])
    const second = phase(24, 'running', [{ type: 'node_start', event_id: 9, step_id: 's2', title: '第二次分析' }])

    jobs.seedPhase('session-1', first)
    jobs.seedPhase('session-1', second)

    expect(jobs.get('job-1')).toMatchObject({ phaseInputId: 24, backendStatus: 'running' })
    expect(jobs.get('job-1')?.thinking.stepOrder).toEqual(['s2'])

    jobs.seedPhase('session-1', first)
    expect(jobs.get('job-1')).toMatchObject({ phaseInputId: 24 })
    expect(jobs.get('job-1')?.thinking.stepOrder).toEqual(['s2'])
  })

  it('本页实时记录只补记输入，不用历史阶段重建', () => {
    setActivePinia(createPinia())
    const jobs = useJobsStore()
    const record = jobs.startFromResponse('job-1', 'session-1', 'running')
    jobs.applyEvent('job-1', event('node_start', 1, { step_id: 'live', title: '实时阶段' }), record.generation)

    jobs.seedPhase('session-1', phase(21, 'running', [{ type: 'node_start', event_id: 1, step_id: 's1', title: '历史阶段' }]))

    expect(jobs.get('job-1')).toMatchObject({ phaseInputId: 21 })
    expect(jobs.get('job-1')?.thinking.stepOrder).toEqual(['live'])
    expect(jobs.get('job-1')?.resumeEventId).toBe(1)
  })

  it('追问恢复交出上一阶段快照，运行态记录从空投影和原游标继续', () => {
    setActivePinia(createPinia())
    const jobs = useJobsStore()
    const record = jobs.startFromResponse('job-1', 'session-1', 'running', 5)
    jobs.applyEvent('job-1', event('node_start', 6, { step_id: 's1', title: '第一次分析' }), record.generation)
    jobs.applyEvent('job-1', event('interrupt', 7, { question_id: 'q-1', message: '请补充' }), record.generation)

    // 追问的真实顺序：先按恢复响应确认状态，再交出上一阶段。
    jobs.startFromResponse('job-1', 'session-1', 'running')
    const snapshot = jobs.startResume('job-1')

    expect(snapshot).toMatchObject({ status: 'waiting_input', stepOrder: ['s1'] })
    expect(jobs.get('job-1')).toMatchObject({ phaseInputId: null, resumeEventId: 7, uiState: 'running' })
    expect(jobs.get('job-1')?.thinking).toMatchObject({ status: 'active', stepOrder: [], waitingInput: null })
  })

  it('追问开始新阶段时把上一阶段记录固定到发起它的用户消息', () => {
    setActivePinia(createPinia())
    const sessions = useSessionsStore()
    sessions.messages = [
      { localId: 'm1', sender: 'user', text: '第一次提问', analysisJobId: 'job-1' },
      { localId: 'm2', sender: 'ai', text: '请补充', analysisJobId: 'job-1' },
    ]
    const record = useJobsStore().startFromResponse('job-1', 'session-1', 'running')
    const snapshot = record.thinking

    sessions.freezeThinking('job-1', snapshot)
    sessions.appendUserMessage('第二次提问', 'job-1')

    expect(sessions.messages[0]).toMatchObject({ localId: 'm1', frozenThinking: snapshot })
    expect(sessions.messages[1]?.frozenThinking).toBeUndefined()
    expect(sessions.messages[2]).toMatchObject({ sender: 'user', text: '第二次提问' })
    expect(sessions.messages[2]?.frozenThinking).toBeUndefined()
  })
})
