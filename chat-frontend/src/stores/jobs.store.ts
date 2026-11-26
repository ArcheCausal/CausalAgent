import { defineStore } from 'pinia'
import type { DecodedSseEvent } from '../api/events.schemas'
import type { ActiveJobResponse } from '../api/jobs.schemas'
import type { BackendJobStatus, ExecutionPhase, InteractionIdentity, JobConnectionState, JobRecord, ThinkingProjection } from '../types/domain'
import { canSubmitInteraction, matchesInteraction } from '../runtime/jobs/interaction'
import {
  createJobRecord,
  reduceJobEvent,
  seedJobFromPhase,
  settleDecision as settleDecisionState,
  startResumePhase,
  waitingInputFromInteraction,
} from '../runtime/jobs/event-reducer'
import { observeActiveEventId } from '../runtime/jobs/event-cursor'

interface JobsState {
  records: Record<string, JobRecord>
}

function isBackendStatus(value: string | undefined): value is BackendJobStatus {
  return value === 'queued' || value === 'running' || value === 'waiting_input' || value === 'succeeded' || value === 'failed' || value === 'canceled'
}

function uiStateFor(status: BackendJobStatus): JobRecord['uiState'] {
  if (status === 'succeeded') return 'completed'
  return status
}

export const useJobsStore = defineStore('jobs', {
  state: (): JobsState => ({ records: {} }),
  getters: {
    byId: (state) => (jobId: string): JobRecord | undefined => state.records[jobId],
    forSession: (state) => (sessionId: string): JobRecord[] => Object.values(state.records).filter((job) => job.sessionId === sessionId),
    activeForSession: (state) => (sessionId: string): JobRecord | null => Object.values(state.records).find((job) => job.sessionId === sessionId && ['queued', 'running', 'waiting_input', 'resuming', 'canceling'].includes(job.uiState)) ?? null,
  },
  actions: {
    get(jobId: string): JobRecord | undefined {
      return this.records[jobId]
    },
    setGeneration(jobId: string, generation: number): void {
      const record = this.records[jobId]
      if (record) record.generation = generation
    },
    setConnection(jobId: string, connection: JobConnectionState): void {
      const record = this.records[jobId]
      if (record) record.connection = connection
    },
    startFromResponse(jobId: string, sessionId: string, status: BackendJobStatus, initialCursor = 0): JobRecord {
      const existing = this.records[jobId]
      if (existing) {
        existing.backendStatus = status
        existing.uiState = uiStateFor(status)
        return existing
      }
      const record = createJobRecord(jobId, sessionId, status, initialCursor)
      this.records[jobId] = record
      return record
    },
    seedPhase(sessionId: string, phase: ExecutionPhase, restore = false): void {
      const jobId = phase.analysisJobId
      if (!jobId) return
      const inputId = phase.analysisJobInputId ?? null
      const existing = this.records[jobId]
      if (!existing || restore) {
        this.records[jobId] = seedJobFromPhase(jobId, sessionId, phase)
        return
      }
      if (existing.phaseInputId === null) {
        // 本页实时创建的记录已经积累了进度，只补记它代表的分析输入。
        if (inputId !== null) existing.phaseInputId = inputId
        return
      }
      // 同一 Job 的后续输入以更靠后的阶段为准；更早的历史阶段不覆盖运行态记录。
      if (inputId === null || inputId <= existing.phaseInputId) return
      this.records[jobId] = seedJobFromPhase(jobId, sessionId, phase)
    },
    /** 追问恢复：把上一阶段的执行记录交回调用方固定，运行态记录从新阶段继续。 */
    startResume(jobId: string): ThinkingProjection | null {
      const record = this.records[jobId]
      if (!record) return null
      const next = startResumePhase(record)
      this.records[jobId] = next.state
      return next.snapshot
    },
    beginResume(identity: InteractionIdentity): boolean {
      const record = this.records[identity.jobId]
      if (!record || !canSubmitInteraction(record, identity)) return false
      record.uiState = 'resuming'
      return true
    },
    failResume(identity: InteractionIdentity): void {
      const record = this.records[identity.jobId]
      if (record && matchesInteraction(record, identity) && record.uiState === 'resuming') record.uiState = 'waiting_input'
    },
    acceptResume(identity: InteractionIdentity, status: BackendJobStatus): ThinkingProjection | null {
      const record = this.records[identity.jobId]
      if (!record || !matchesInteraction(record, identity)
        || !['waiting', 'answered'].includes(record.thinking.waitingInput?.status ?? '')
        || ['completed', 'failed', 'canceled'].includes(record.uiState)) return null
      const next = startResumePhase(record)
      next.state.backendStatus = status
      // 响应可能已是终态，仍需从原游标回放结果事件后才能停止订阅。
      next.state.uiState = status === 'queued' ? 'queued' : 'running'
      this.records[identity.jobId] = next.state
      return next.snapshot
    },
    reconcileSessionJobs(sessionId: string, activeJobIds: string[]): void {
      const active = new Set(activeJobIds)
      for (const record of Object.values(this.records)) {
        if (record.sessionId === sessionId && !active.has(record.jobId)) delete this.records[record.jobId]
      }
    },
    observeActive(job: ActiveJobResponse): JobRecord {
      const status = isBackendStatus(job.status) ? job.status : 'queued'
      const interaction = (job.current_interaction ?? {}) as Record<string, unknown>
      const waitingInput = status === 'waiting_input' && job.current_question_id
        ? waitingInputFromInteraction(
            interaction,
            job.current_question_id,
            job.current_waiting_prompt ?? '',
          )
        : null
      if (waitingInput && job.input_wait_expires_at) waitingInput.expiresAt = job.input_wait_expires_at
      const existing = this.records[job.job_id]
      if (!existing) {
        const record = createJobRecord(job.job_id, job.session_id, status, 0)
        record.observedLastEventId = job.last_event_id ?? null
        if (waitingInput) {
          record.thinking.status = 'waiting_input'
          record.thinking.waitingInput = waitingInput
        }
        this.records[job.job_id] = record
        return record
      }
      existing.backendStatus = status
      existing.uiState = uiStateFor(status)
      const cursor = observeActiveEventId(existing, job.last_event_id)
      existing.observedLastEventId = cursor.observedLastEventId
      if (waitingInput) {
        existing.thinking = {
          ...existing.thinking,
          status: 'waiting_input',
          waitingInput,
        }
      } else if (existing.thinking.waitingInput?.status === 'waiting') {
        existing.thinking.waitingInput.status = status === 'queued' || status === 'running' || status === 'succeeded' ? 'answered' : 'closed'
        existing.thinking.status = status === 'succeeded' ? 'completed' : status === 'failed' ? 'failed' : status === 'canceled' ? 'canceled' : 'active'
      }
      return existing
    },
    chooseProcessingOption(jobId: string, optionId: string): void {
      const record = this.records[jobId]
      if (!record?.thinking.waitingInput) return
      record.thinking.waitingInput.selectedOptionId = optionId
    },
    applyEvent(jobId: string, event: DecodedSseEvent, generation: number): ReturnType<typeof reduceJobEvent> | null {
      const existing = this.records[jobId]
      if (!existing || existing.generation !== generation) return null
      const result = reduceJobEvent(existing, event)
      this.records[jobId] = result.state
      return result
    },
    settleDecision(jobId: string, stepId: string, key: string): void {
      const existing = this.records[jobId]
      if (!existing) return
      this.records[jobId] = settleDecisionState(existing, stepId, key)
    },
    markSubscriptionError(jobId: string, message: string, code: string, generation: number): void {
      const record = this.records[jobId]
      if (!record || record.generation !== generation) return
      record.connection = 'closed'
      record.errorMessage = message
      record.errorCode = code
    },
    markTerminalConflict(jobId: string, status: BackendJobStatus, generation: number): void {
      const record = this.records[jobId]
      if (!record || record.generation !== generation) return
      record.backendStatus = status
      record.uiState = uiStateFor(status)
      record.thinking = {
        ...record.thinking,
        status: status === 'succeeded' ? 'completed' : 'failed',
        errorMessage: status === 'failed' ? '任务执行失败' : record.thinking.errorMessage,
      }
    },
    beginCancel(jobId: string, key: string): string | null {
      const record = this.records[jobId]
      if (!record || record.cancelInFlight) return record?.cancelKey ?? null
      record.cancelKey = key
      record.cancelInFlight = true
      record.uiState = 'canceling'
      return key
    },
    finishCancel(jobId: string, key: string): void {
      const record = this.records[jobId]
      if (!record || record.cancelKey !== key) return
      record.cancelKey = null
      record.cancelInFlight = false
      if (record.backendStatus === 'queued' || record.backendStatus === 'running' || record.backendStatus === 'waiting_input') record.uiState = record.backendStatus
    },
    applySyntheticTerminal(jobId: string, status: 'canceled' | 'completed' | 'failed', message = ''): void {
      const record = this.records[jobId]
      if (!record) return
      const backendStatus: BackendJobStatus = status === 'completed' ? 'succeeded' : status
      record.backendStatus = backendStatus
      record.uiState = status
      record.thinking = {
        ...record.thinking,
        status: status === 'completed' ? 'completed' : status,
        errorMessage: status === 'failed' ? message : record.thinking.errorMessage,
        waitingInput: null,
      }
    },
    reset(): void {
      this.records = {}
    },
  },
})
