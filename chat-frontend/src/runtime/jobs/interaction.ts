import type { InteractionIdentity, JobRecord, WaitingInput } from '../../types/domain'

export function interactionIdentity(job: JobRecord): InteractionIdentity | null {
  const waiting = job.thinking.waitingInput
  return waiting ? { jobId: job.jobId, questionId: waiting.questionId, planVersion: waiting.planVersion } : null
}

export function matchesInteraction(job: JobRecord | null | undefined, identity: InteractionIdentity): boolean {
  const current = job ? interactionIdentity(job) : null
  return Boolean(current && current.jobId === identity.jobId
    && current.questionId === identity.questionId && current.planVersion === identity.planVersion)
}

export function isWaitingExpired(waiting: WaitingInput, now = Date.now()): boolean {
  if (waiting.status !== 'waiting') return waiting.status === 'expired'
  const deadline = waiting.expiresAt ? Date.parse(waiting.expiresAt) : NaN
  return waiting.expired || (Number.isFinite(deadline) && deadline <= now)
}

export function canSubmitInteraction(job: JobRecord | null | undefined, identity: InteractionIdentity): boolean {
  return Boolean(job && matchesInteraction(job, identity) && job.uiState === 'waiting_input'
    && job.thinking.waitingInput?.status === 'waiting' && !isWaitingExpired(job.thinking.waitingInput))
}
