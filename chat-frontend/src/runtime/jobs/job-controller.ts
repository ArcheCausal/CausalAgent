import { api } from '../../api/client'
import { ApiError } from '../../api/errors'
import type { DecodedSseEvent } from '../../api/events.schemas'
import { SseProtocolError } from '../../api/events.schemas'
import type { BackendJobStatus, JobRecord } from '../../types/domain'
import { reduceJobEvent } from './event-reducer'
import { createIdempotencyKey, retryIdempotent } from './idempotency'
import { JobSseTransport } from './sse-transport'
import { createGenerationState, invalidateGeneration, isCurrentGeneration, nextGeneration } from './subscription-generation'

export interface JobsPort {
  get(jobId: string): JobRecord | undefined
  setConnection(jobId: string, connection: JobRecord['connection']): void
  setGeneration(jobId: string, generation: number): void
  applyEvent(jobId: string, event: DecodedSseEvent, generation: number): ReturnType<typeof reduceJobEvent> | null
  markSubscriptionError(jobId: string, message: string, code: string, generation: number): void
  markTerminalConflict(jobId: string, status: BackendJobStatus, generation: number): void
  beginCancel(jobId: string, key: string): string | null
  finishCancel(jobId: string, key: string): void
  applySyntheticTerminal(jobId: string, status: 'canceled' | 'completed' | 'failed', message?: string): void
}

interface RuntimeSubscription {
  generation: number
  controller: AbortController
}

export class JobController {
  private readonly subscriptions = new Map<string, RuntimeSubscription>()
  private readonly generations = createGenerationState()

  constructor(
    private readonly jobs: JobsPort,
    private readonly transport = new JobSseTransport(),
  ) {}

  subscribe(jobId: string): Promise<void> {
    this.stop(jobId)
    const generation = nextGeneration(this.generations, jobId)
    this.jobs.setGeneration(jobId, generation)
    const controller = new AbortController()
    this.subscriptions.set(jobId, { generation, controller })
    const runtime = this.jobs.get(jobId)
    const promise = this.transport.stream(jobId, {
      initialCursor: runtime?.resumeEventId ?? 0,
      signal: controller.signal,
      getCursor: () => this.jobs.get(jobId)?.transportCursor ?? 0,
      shouldStop: () => {
        const current = this.jobs.get(jobId)
        return Boolean(current && ['completed', 'failed', 'canceled', 'waiting_input'].includes(current.uiState))
      },
      onConnectionState: (state) => {
        if (isCurrentGeneration(this.generations, jobId, generation)) this.jobs.setConnection(jobId, state)
      },
    }, (event) => {
      if (controller.signal.aborted) return
      if (!isCurrentGeneration(this.generations, jobId, generation)) {
        controller.abort()
        return
      }
      const result = this.jobs.applyEvent(jobId, event, generation)
      if (result?.terminal) controller.abort()
    })
    return promise.catch((error: unknown) => {
      if (controller.signal.aborted || !isCurrentGeneration(this.generations, jobId, generation)) return
      if (error instanceof SseProtocolError) {
        this.jobs.markSubscriptionError(jobId, '事件流协议错误，请刷新后重试。', error.code, generation)
      } else if (error instanceof ApiError) {
        this.jobs.markSubscriptionError(jobId, error.message, error.code, generation)
      } else {
        this.jobs.markSubscriptionError(jobId, '任务连接失败，请刷新后重试。', 'sse_transport_error', generation)
      }
      throw error
    }).finally(() => {
      const current = this.subscriptions.get(jobId)
      if (current?.generation === generation) this.subscriptions.delete(jobId)
    })
  }

  stop(jobId: string): void {
    const current = this.subscriptions.get(jobId)
    current?.controller.abort()
    this.subscriptions.delete(jobId)
    invalidateGeneration(this.generations, jobId)
  }

  stopAll(): void {
    for (const jobId of this.subscriptions.keys()) this.stop(jobId)
  }

  async cancel(jobId: string): Promise<void> {
    const key = createIdempotencyKey()
    const cancelKey = this.jobs.beginCancel(jobId, key)
    if (!cancelKey) return
    try {
      const response = await retryIdempotent(
        () => api.cancelJob(jobId, cancelKey),
        { maxRetries: 1 },
      )
      this.stop(jobId)
      this.jobs.applySyntheticTerminal(jobId, response.status === 'canceled' ? 'canceled' : 'completed', '任务已取消')
    } catch (error) {
      if (error instanceof ApiError && error.status === 409 && error.code === 'job_state_conflict') {
        const details = error.details
        const status = typeof details === 'object' && details !== null && 'status' in details && typeof details.status === 'string'
          ? details.status
          : null
        if (status === 'canceled') {
          this.stop(jobId)
          this.jobs.applySyntheticTerminal(jobId, 'canceled', '任务已取消')
        } else if (status === 'succeeded' || status === 'failed') {
          this.stop(jobId)
          this.jobs.markTerminalConflict(jobId, status, this.jobs.get(jobId)?.generation ?? 0)
        }
      }
      throw error
    } finally {
      this.jobs.finishCancel(jobId, cancelKey)
    }
  }
}
