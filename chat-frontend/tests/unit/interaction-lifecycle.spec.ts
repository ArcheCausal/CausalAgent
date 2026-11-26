import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'
import { useJobsStore } from '../../src/stores/jobs.store'
import { canSubmitInteraction, interactionIdentity, isWaitingExpired } from '../../src/runtime/jobs/interaction'

describe('交互提交生命周期', () => {
  beforeEach(() => setActivePinia(createPinia()))

  function waiting(questionId = 'question-1', planVersion = 'a'.repeat(64), deadline = new Date(Date.now() + 300000).toISOString()) {
    const jobs = useJobsStore()
    const job = jobs.observeActive({
      job_id: 'job-1', session_id: 'session-1', status: 'waiting_input',
      current_question_id: questionId, current_waiting_prompt: '确认方案',
      input_wait_expires_at: deadline,
      current_interaction: { interaction_type: 'data_processing_plan_confirmation', plan_version: planVersion },
    })
    const identity = interactionIdentity(job)
    if (!identity) throw new Error('当前交互不存在')
    return { jobs, job, identity, deadline }
  }

  it('服务端成功前保留等待内容，失败后允许按原身份重试', () => {
    const { jobs, job, identity, deadline } = waiting()
    expect(jobs.beginResume(identity)).toBe(true)
    expect(jobs.beginResume(identity)).toBe(false)
    expect(job.thinking.waitingInput).toMatchObject({ status: 'waiting', expiresAt: deadline })
    jobs.failResume(identity)
    expect(canSubmitInteraction(job, identity)).toBe(true)
    expect(job.thinking.waitingInput).toMatchObject({ status: 'waiting', expiresAt: deadline })
  })

  it('成功后快照为已回答，即使经过原期限也不变成过期', async () => {
    const { jobs, identity } = waiting('question-1', 'a'.repeat(64), new Date(Date.now() + 1000).toISOString())
    expect(jobs.beginResume(identity)).toBe(true)
    const snapshot = jobs.acceptResume(identity, 'queued')
    expect(snapshot?.waitingInput?.status).toBe('answered')
    await new Promise((resolve) => setTimeout(resolve, 1100))
    expect(snapshot?.waitingInput?.status).toBe('answered')
    expect(snapshot?.waitingInput && isWaitingExpired(snapshot.waitingInput)).toBe(false)
    expect(jobs.get('job-1')?.thinking.waitingInput).toBeNull()
  })

  it('Job、问题或方案版本不同均不能提交，迟到响应不能清除新问题', () => {
    const { jobs, job, identity } = waiting()
    for (const stale of [{ ...identity, jobId: 'old-job' }, { ...identity, questionId: 'old-question' }, { ...identity, planVersion: 'b'.repeat(64) }]) {
      expect(canSubmitInteraction(job, stale)).toBe(false)
      expect(jobs.beginResume(stale)).toBe(false)
    }
    jobs.beginResume(identity)
    waiting('question-2', 'b'.repeat(64))
    jobs.failResume(identity)
    expect(jobs.acceptResume(identity, 'queued')).toBeNull()
    expect(jobs.get('job-1')?.thinking.waitingInput).toMatchObject({ questionId: 'question-2', status: 'waiting' })
  })

  it('刷新和活动摘要重复恢复始终使用服务端截止时间', () => {
    const deadline = new Date(Date.now() + 250000).toISOString()
    const { jobs } = waiting('question-1', 'a'.repeat(64), deadline)
    waiting('question-1', 'a'.repeat(64), deadline)
    expect(jobs.get('job-1')?.thinking.waitingInput?.expiresAt).toBe(deadline)
    jobs.reset()
    expect(waiting('question-1', 'a'.repeat(64), deadline).job.thinking.waitingInput?.expiresAt).toBe(deadline)
  })

  it.each(['running', 'waiting_input'])('历史仍为 %s 但活动摘要已移除任务时，保留游标回放最终事件', (status) => {
    const jobs = useJobsStore()
    jobs.seedPhase('session-1', { phaseSequence: 1, status, elapsedSeconds: 1, lastEventId: 10, analysisJobId: 'job-1', analysisJobInputId: 21, events: [] }, true)
    jobs.reconcileSessionJobs('session-1', ['job-1'])
    const job = jobs.get('job-1')
    if (!job) throw new Error('待回放记录被提前移除')
    expect(job.resumeEventId).toBe(10)
    const result = jobs.applyEvent(job.jobId, { event: 'final_result', id: 11, known: true, data: { type: 'final_result', data: { type: 'text', summary: '最终报告' } } }, job.generation)
    expect(result?.terminal).toBe(true)
    expect(jobs.get(job.jobId)?.thinking.finalResult?.summary).toBe('最终报告')
  })

  it('已过期的问题不能从输入框路径提交', () => {
    const { jobs, identity } = waiting('question-1', 'a'.repeat(64), new Date(Date.now() - 1000).toISOString())
    expect(jobs.beginResume(identity)).toBe(false)
  })

  it.each(['succeeded', 'failed', 'canceled'] as const)('恢复响应已为 %s 时仍回放最终事件', (status) => {
    const { jobs, job, identity } = waiting()
    jobs.beginResume(identity)
    expect(jobs.acceptResume(identity, status)?.waitingInput?.status).toBe('answered')
    expect(jobs.get(job.jobId)?.uiState).toBe('running')
    const type = status === 'succeeded' ? 'final_result' : status === 'failed' ? 'error' : 'canceled'
    const result = jobs.applyEvent(job.jobId, { event: type, id: 2, known: true, data: { type, data: { type: 'text', summary: '最终报告' }, message: '终态说明' } }, job.generation)
    expect(result?.applied).toBe(true)
    expect(jobs.get(job.jobId)?.uiState).toBe(status === 'succeeded' ? 'completed' : status)
    if (status === 'succeeded') expect(jobs.get(job.jobId)?.thinking.finalResult?.summary).toBe('最终报告')
  })

  it('活动摘要先报告恢复执行时，后到的响应仍能固定旧阶段并清空投影', () => {
    const { jobs, identity } = waiting()
    jobs.beginResume(identity)
    jobs.observeActive({ job_id: identity.jobId, session_id: 'session-1', status: 'running' })
    expect(jobs.get(identity.jobId)?.thinking.status).toBe('active')
    const frozen = jobs.acceptResume(identity, 'queued')
    expect(frozen?.waitingInput?.status).toBe('answered')
    expect(jobs.get(identity.jobId)?.thinking.waitingInput).toBeNull()
  })

  it('切回会话时以最新历史输入重建活动投影，旧响应不能再次清空它', () => {
    const { jobs, identity } = waiting()
    jobs.beginResume(identity)
    jobs.seedPhase('session-1', { phaseSequence: 2, status: 'running', elapsedSeconds: 1, lastEventId: 10, analysisJobId: identity.jobId, analysisJobInputId: 24, events: [] }, true)
    jobs.observeActive({ job_id: identity.jobId, session_id: 'session-1', status: 'running' })
    expect(jobs.get(identity.jobId)?.phaseInputId).toBe(24)
    expect(jobs.get(identity.jobId)?.thinking.waitingInput).toBeNull()
    expect(jobs.acceptResume(identity, 'queued')).toBeNull()
    jobs.reconcileSessionJobs('session-1', [])
    expect(jobs.activeForSession('session-1')).toBeNull()
    expect(jobs.get(identity.jobId)).toBeUndefined()
  })

  it.each([['error', 'human_input_timeout', 'expired'], ['error', 'worker_error', 'closed'], ['canceled', '', 'closed']])('终态 %s/%s 保留问题且结束交互为 %s', (type, reason, status) => {
    const { jobs, job } = waiting()
    jobs.applyEvent(job.jobId, { event: type, id: 1, known: true, data: { type, reason_code: reason, message: '任务已结束' } }, job.generation)
    expect(jobs.get(job.jobId)?.thinking.waitingInput?.status).toBe(status)
    expect(jobs.activeForSession('session-1')).toBeNull()
  })
})
