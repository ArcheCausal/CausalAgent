import type {
  BackendJobStatus,
  JobRecord,
  PublicEvent,
  StepDecisionDetail,
  StepDetail,
  StructuredMessage,
  ThinkingProjection,
  ThinkingStep,
} from '../../types/domain'
import type { DecodedSseEvent } from '../../api/events.schemas'
import { acceptEventId, createEventCursor, markRendered } from './event-cursor'
import {
  appendDetails,
  decisionDetail,
  decisionStreamKey,
  findDecisionDetail,
  numberField,
  openDecisionForTool,
  releaseDecisionDetail,
  replaceDecisionDetail,
  stepDetailText,
  stringField,
  textDetail,
} from './step-details'

export interface ReducerResult {
  state: JobRecord
  applied: boolean
  duplicate: boolean
  unknown: boolean
  terminal: boolean
}

function createThinking(status: ThinkingProjection['status'] = 'active', elapsedSeconds = 0): ThinkingProjection {
  return {
    status,
    startedAt: Date.now() - Math.max(0, elapsedSeconds) * 1000,
    elapsedSeconds: Math.max(0, elapsedSeconds),
    steps: {},
    stepOrder: [],
    pendingStepEvents: {},
    draftStreamId: null,
    draftText: '',
    finalResult: null,
    errorMessage: null,
    waitingInput: null,
  }
}

function cloneDetail(detail: StepDetail): StepDetail {
  return detail.kind === 'decision'
    ? { ...detail, pending: [...detail.pending] }
    : { ...detail }
}

function clonePendingStepEvents(
  events: ThinkingProjection['pendingStepEvents'],
): ThinkingProjection['pendingStepEvents'] {
  const pending: ThinkingProjection['pendingStepEvents'] = {}
  for (const [stepId, list] of Object.entries(events)) {
    pending[stepId] = list.map((data) => ({ ...data }))
  }
  return pending
}

function cloneThinking(thinking: ThinkingProjection): ThinkingProjection {
  const steps: Record<string, ThinkingStep> = {}
  for (const [key, step] of Object.entries(thinking.steps)) {
    steps[key] = { ...step, details: step.details.map(cloneDetail) }
  }
  return {
    ...thinking,
    steps,
    stepOrder: [...thinking.stepOrder],
    pendingStepEvents: clonePendingStepEvents(thinking.pendingStepEvents),
    waitingInput: thinking.waitingInput ? { ...thinking.waitingInput } : null,
    finalResult: thinking.finalResult ? { ...thinking.finalResult } : null,
  }
}

export function createJobRecord(
  jobId: string,
  sessionId: string,
  status: BackendJobStatus = 'queued',
  initialCursor = 0,
): JobRecord {
  const cursor = createEventCursor(initialCursor)
  const uiState = status === 'queued' ? 'queued' : status === 'running' ? 'running' : status === 'waiting_input' ? 'waiting_input' : status === 'succeeded' ? 'completed' : status
  return {
    jobId,
    sessionId,
    phaseInputId: null,
    backendStatus: status,
    uiState,
    connection: 'idle',
    generation: 0,
    ...cursor,
    events: [],
    textSeenKeys: [],
    textStreams: {},
    thinking: createThinking(status === 'waiting_input' ? 'waiting_input' : status === 'succeeded' ? 'completed' : status === 'failed' ? 'failed' : status === 'canceled' ? 'canceled' : 'active'),
    errorMessage: null,
    errorCode: null,
    cancelKey: null,
    cancelInFlight: false,
  }
}

function structuredResult(value: unknown): StructuredMessage {
  if (typeof value === 'object' && value !== null && 'type' in value) {
    const candidate = value as Record<string, unknown>
    if (typeof candidate.type === 'string') return { ...candidate, type: candidate.type }
  }
  return {
    type: 'text',
    summary: typeof value === 'string' ? value : JSON.stringify(value),
  }
}

function releaseTextBuffers(state: JobRecord): void {
  state.textSeenKeys = []
  state.textStreams = {}
}

function ensureStep(thinking: ThinkingProjection, data: PublicEvent): ThinkingStep | null {
  const stepId = stringField(data, 'step_id')
  if (!stepId) return null
  const existing = thinking.steps[stepId]
  if (existing) return existing
  const step: ThinkingStep = {
    stepId,
    nodeName: stringField(data, 'node_name'),
    title: stringField(data, 'title') || stringField(data, 'node_name'),
    status: 'in-progress',
    duration: null,
    details: [],
  }
  thinking.steps[stepId] = step
  thinking.stepOrder.push(stepId)
  return step
}

function findStep(thinking: ThinkingProjection, data: Record<string, unknown>): ThinkingStep | null {
  const stepId = stringField(data, 'step_id')
  if (!stepId) return null
  return thinking.steps[stepId] ?? null
}

/** 父阶段尚未到达时暂存明细，等同一 step_id 出现后按到达顺序补绘一次。 */
function deferStepEvent(thinking: ThinkingProjection, data: Record<string, unknown>): void {
  const stepId = stringField(data, 'step_id')
  if (!stepId) return
  const pending = thinking.pendingStepEvents[stepId] ?? []
  thinking.pendingStepEvents[stepId] = [...pending, { ...data }]
}

function takeDeferredStepEvents(thinking: ThinkingProjection, stepId: string): Array<Record<string, unknown>> {
  const pending = thinking.pendingStepEvents[stepId]
  if (!pending || pending.length === 0) return []
  const rest: ThinkingProjection['pendingStepEvents'] = { ...thinking.pendingStepEvents }
  delete rest[stepId]
  thinking.pendingStepEvents = rest
  return pending
}

/** 收到完整 decision 只结束该决策流，工具事件仍等展示追平后再放行。 */
function completeDecision(step: ThinkingStep, entry: StepDecisionDetail): void {
  replaceDecisionDetail(step, entry, { ...entry, complete: true })
}

/** 终态或异常兜底：不再等待展示，直接放行所有未结束的公开决策。 */
function releaseAllDecisions(thinking: ThinkingProjection): void {
  for (const step of Object.values(thinking.steps)) {
    for (const detail of [...step.details]) {
      if (detail.kind === 'decision' && !detail.released) releaseDecisionDetail(step, detail)
    }
  }
}

function applyStepDetail(thinking: ThinkingProjection, data: Record<string, unknown>): void {
  const step = findStep(thinking, data)
  if (!step) {
    deferStepEvent(thinking, data)
    return
  }
  if (data.type === 'decision') {
    const decisionKind = stringField(data, 'decision_kind')
    const toolName = stringField(data, 'tool_name')
    if (decisionKind !== 'final' && toolName) {
      const entry = findDecisionDetail(step, decisionStreamKey(step.stepId, decisionKind, toolName))
      if (entry) {
        completeDecision(step, entry)
        return
      }
    }
    appendDetails(step, [textDetail(stepDetailText(data))])
    return
  }
  if (data.type === 'tool_call_start' || data.type === 'tool_call_result') {
    const held = openDecisionForTool(step, stringField(data, 'tool_name'))
    if (held) {
      const next = { ...held, pending: [...held.pending, { ...data }] }
      replaceDecisionDetail(step, held, next)
      if (data.type === 'tool_call_result') {
        // 异常或旧端点没有完整 decision 结束事件时，不能永久挂起工具结果。
        releaseDecisionDetail(step, next)
      }
      return
    }
  }
  appendDetails(step, [textDetail(stepDetailText(data))])
}

function appendTextDelta(state: JobRecord, data: PublicEvent): { duplicate: boolean; buffer: string } {
  const streamId = stringField(data, 'stream_id')
  const sequence = numberField(data, 'sequence')
  const stepId = stringField(data, 'step_id')
  const key = `${stepId}:${streamId}:${sequence ?? ''}`
  if (state.textSeenKeys.includes(key)) {
    return { duplicate: true, buffer: state.textStreams[streamId] ?? '' }
  }
  state.textSeenKeys = [...state.textSeenKeys, key]
  const buffer = `${state.textStreams[streamId] ?? ''}${stringField(data, 'delta')}`
  state.textStreams = { ...state.textStreams, [streamId]: buffer }
  return { duplicate: false, buffer }
}

function applyKnownEvent(state: JobRecord, event: DecodedSseEvent): { state: JobRecord; visible: boolean; terminal: boolean } {
  const data = event.data
  const thinking = cloneThinking(state.thinking)
  const next: JobRecord = {
    ...state,
    thinking,
    events: event.id === null ? [...state.events] : [...state.events, { id: event.id, type: data.type, data: { ...data } }],
  }
  let visible = true
  let terminal = false

  switch (data.type) {
    case 'heartbeat':
      visible = false
      break
    case 'node_start': {
      const step = ensureStep(thinking, data)
      if (step) {
        for (const pending of takeDeferredStepEvents(thinking, step.stepId)) applyStepDetail(thinking, pending)
      }
      break
    }
    case 'progress':
    case 'decision':
    case 'tool_call_start':
    case 'tool_call_result':
      applyStepDetail(thinking, data)
      break
    case 'decision_delta': {
      const step = findStep(thinking, data)
      if (!step) {
        deferStepEvent(thinking, data)
        break
      }
      const appended = appendTextDelta(next, data)
      if (appended.duplicate) {
        visible = false
        break
      }
      const streamId = stringField(data, 'stream_id')
      const entry = findDecisionDetail(
        step,
        decisionStreamKey(step.stepId, stringField(data, 'decision_kind'), stringField(data, 'tool_name')),
      )
      if (!entry) {
        appendDetails(step, [decisionDetail(step.stepId, data, appended.buffer)])
      } else if (entry.streamId === streamId) {
        replaceDecisionDetail(step, entry, { ...entry, text: appended.buffer })
      } else {
        // 同一决策键重新开流（重试）时以新流为准，不拼接旧增量。
        replaceDecisionDetail(step, entry, { ...entry, streamId, text: appended.buffer, complete: false, pending: [] })
      }
      break
    }
    case 'node_retry': {
      const step = ensureStep(thinking, data)
      if (step) {
        step.status = 'in-progress'
        appendDetails(step, [
          textDetail(`调用失败：${stringField(data, 'message')}`, 'error'),
          textDetail('正在重试', 'retry'),
        ])
      }
      const discarded = stringField(data, 'discard_stream_id')
      if (discarded) {
        const streams = { ...next.textStreams }
        delete streams[discarded]
        next.textStreams = streams
        if (thinking.draftStreamId === discarded) {
          thinking.draftStreamId = null
          thinking.draftText = ''
        }
      }
      break
    }
    case 'node_end': {
      const step = ensureStep(thinking, data)
      if (step) {
        step.status = data.status === 'failed' ? 'failed' : 'completed'
        step.duration = numberField(data, 'duration')
        const message = stringField(data, 'message')
        if (data.status === 'failed' && message) appendDetails(step, [textDetail(`调用失败：${message}`, 'error')])
      }
      break
    }
    case 'text_delta': {
      const appended = appendTextDelta(next, data)
      if (appended.duplicate) {
        visible = false
        break
      }
      thinking.draftStreamId = stringField(data, 'stream_id')
      thinking.draftText = appended.buffer
      break
    }
    case 'final_result': {
      const result = structuredResult(data.data)
      thinking.status = 'completed'
      releaseAllDecisions(thinking)
      if (thinking.draftStreamId !== null && result.type === 'text') {
        // 公开文字流的终态只校正已有草稿；报告布局同样复用草稿，不做第二次渲染。
        thinking.draftText = typeof result.summary === 'string' ? result.summary : ''
      } else {
        thinking.draftStreamId = null
        thinking.draftText = ''
        thinking.finalResult = result
      }
      next.backendStatus = 'succeeded'
      next.uiState = 'completed'
      releaseTextBuffers(next)
      terminal = true
      break
    }
    case 'interrupt':
      thinking.status = 'waiting_input'
      thinking.waitingInput = { questionId: stringField(data, 'question_id'), prompt: stringField(data, 'message') }
      next.backendStatus = 'waiting_input'
      next.uiState = 'waiting_input'
      terminal = true
      break
    case 'error':
      thinking.status = 'failed'
      releaseAllDecisions(thinking)
      thinking.errorMessage = stringField(data, 'message')
      next.errorMessage = thinking.errorMessage
      next.errorCode = 'job_error'
      next.backendStatus = 'failed'
      next.uiState = 'failed'
      releaseTextBuffers(next)
      terminal = true
      break
    case 'canceled':
      thinking.status = 'canceled'
      releaseAllDecisions(thinking)
      next.backendStatus = 'canceled'
      next.uiState = 'canceled'
      releaseTextBuffers(next)
      terminal = true
      break
    default:
      visible = false
      break
  }
  return { state: next, visible, terminal }
}

export function reduceJobEvent(state: JobRecord, event: DecodedSseEvent): ReducerResult {
  const accepted = acceptEventId(state, event.id)
  if (!accepted.accepted) return { state, applied: false, duplicate: accepted.duplicate, unknown: false, terminal: false }
  let next: JobRecord = { ...state, ...accepted.cursor }
  if (!event.known) {
    return { state: next, applied: false, duplicate: false, unknown: true, terminal: false }
  }
  if (state.uiState === 'completed' || state.uiState === 'failed' || state.uiState === 'canceled') {
    return { state: next, applied: false, duplicate: false, unknown: false, terminal: false }
  }
  const result = applyKnownEvent(next, event)
  next = result.state
  if (result.visible && event.id !== null) next = { ...next, ...markRendered(next, event.id) }
  return { state: next, applied: result.visible, duplicate: false, unknown: false, terminal: result.terminal }
}

/**
 * 展示层报告某条公开决策的逐字展示已经追平：放行它挂起的工具事件。
 * 重复报告或未收到完整 decision 时保持原状态，保证同一次调用只放行一次。
 */
export function settleDecision(state: JobRecord, stepId: string, key: string): JobRecord {
  const thinking = cloneThinking(state.thinking)
  const step = thinking.steps[stepId]
  if (!step) return state
  const entry = findDecisionDetail(step, key)
  if (!entry || entry.released || !entry.complete) return state
  releaseDecisionDetail(step, entry)
  return { ...state, thinking }
}

export function seedJobFromPhase(
  jobId: string,
  sessionId: string,
  phase: { status: string; elapsedSeconds: number; lastEventId: number; events: Array<Record<string, unknown>>; analysisJobInputId?: number },
): JobRecord {
  const status: BackendJobStatus = phase.status === 'queued' || phase.status === 'running' || phase.status === 'waiting_input' || phase.status === 'succeeded' || phase.status === 'failed' || phase.status === 'canceled' ? phase.status : 'running'
  let state = createJobRecord(jobId, sessionId, status, 0)
  state = { ...state, thinking: createThinking(status === 'waiting_input' ? 'waiting_input' : status === 'succeeded' ? 'completed' : status === 'failed' ? 'failed' : status === 'canceled' ? 'canceled' : 'active', phase.elapsedSeconds) }
  for (const item of phase.events) {
    const eventId = typeof item.event_id === 'number' ? item.event_id : null
    const type = typeof item.type === 'string' ? item.type : ''
    if (!type) continue
    const decoded: DecodedSseEvent = { event: type, id: eventId, data: item as PublicEvent, known: true }
    const result = reduceJobEvent(state, decoded)
    state = result.state
  }
  const cursor = createEventCursor(phase.lastEventId)
  return {
    ...state,
    ...cursor,
    phaseInputId: phase.analysisJobInputId ?? null,
    renderedEventId: phase.lastEventId,
    backendStatus: status,
    uiState: status === 'queued' ? 'queued' : status === 'running' ? 'running' : status === 'waiting_input' ? 'waiting_input' : status === 'succeeded' ? 'completed' : status,
  }
}

/**
 * 追问恢复：上一阶段的执行记录被固定到它所属的用户消息上，运行态记录从空投影继续推进。
 * 游标、文本去重缓冲和连接状态保持不动，新阶段从同一条事件流接着读。
 */
export function startResumePhase(state: JobRecord): { snapshot: ThinkingProjection; state: JobRecord } {
  return {
    snapshot: cloneThinking(state.thinking),
    state: { ...state, phaseInputId: null, thinking: createThinking('active') },
  }
}
