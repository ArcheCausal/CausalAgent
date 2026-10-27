<script setup lang="ts">
import { computed, watch } from 'vue'
import { isPresentationActive } from '../runtime/chat/presentation'
import { useChatScrollFollow } from '../runtime/chat/scroll-follow'
import { stepDetailText, textDetail } from '../runtime/jobs/step-details'
import { waitingInputFromInteraction } from '../runtime/jobs/event-reducer'
import { interactionIdentity } from '../runtime/jobs/interaction'
import { useJobsStore } from '../stores/jobs.store'
import type { ChatMessage, ExecutionPhase, InteractionIdentity, JobRecord, ProcessingOptionAction, StepDetail, ThinkingProjection, WaitingInput } from '../types/domain'
import MessageBody from './MessageBody.vue'
import ProcessingOptions from './ProcessingOptions.vue'
import StreamingDraft from './StreamingDraft.vue'
import ThinkingTimeline from './ThinkingTimeline.vue'

const props = defineProps<{
  messages: ChatMessage[]
  submittingOptionId?: string | null
  activeJob?: JobRecord | null
  interactionSubmitting?: boolean
  interactionError?: string | null
}>()
const emit = defineEmits<{
  'choose-processing-option': [action: ProcessingOptionAction]
  'use-other-requirement': [identity: InteractionIdentity]
}>()
const jobs = useJobsStore()
const scrollFollow = useChatScrollFollow()

function onDecisionSettled(jobId: string | undefined, payload: { stepId: string; key: string }): void {
  if (!jobId) return
  jobs.settleDecision(jobId, payload.stepId, payload.key)
}

function fileExtension(filename: string): string {
  const extension = filename.split('.').pop()?.trim()
  return extension ? extension.toUpperCase() : 'FILE'
}

/** 只有已持久化的历史阶段事件会走到这里；公开决策在历史中本来就是完整文本。 */
function waitingForPhase(phase: ExecutionPhase): ThinkingProjection['waitingInput'] {
  const interrupt = [...phase.events].reverse().find((event) => event.type === 'interrupt')
  if (!interrupt) return null
  return waitingInputFromInteraction(
    interrupt,
    typeof interrupt.question_id === 'string' ? interrupt.question_id : '',
    typeof interrupt.message === 'string' ? interrupt.message : '',
  )
}

function phaseThinking(phase: ExecutionPhase): ThinkingProjection {
  const steps: ThinkingProjection['steps'] = {}
  const order: string[] = []
  const detailsFor = (event: Record<string, unknown>): StepDetail[] => {
    if (event.type === 'node_retry') {
      const message = typeof event.message === 'string' ? event.message : ''
      return [textDetail(`调用失败：${message}`, 'error'), textDetail('正在重试', 'retry')]
    }
    if (event.type === 'node_end') {
      const message = typeof event.message === 'string' ? event.message : ''
      return event.status === 'failed' && message ? [textDetail(`调用失败：${message}`, 'error')] : []
    }
    const detail = stepDetailText(event)
    return detail ? [textDetail(detail)] : []
  }
  for (const event of phase.events) {
    const stepId = typeof event.step_id === 'string' ? event.step_id : ''
    if (!stepId) continue
    let step = steps[stepId]
    if (!step) {
      step = {
        stepId,
        nodeName: typeof event.node_name === 'string' ? event.node_name : '',
        title: typeof event.title === 'string' ? event.title : '',
        status: 'in-progress',
        duration: null,
        details: [],
      }
      steps[stepId] = step
      order.push(stepId)
    }
    if (event.type === 'node_end') {
      step.status = event.status === 'failed' ? 'failed' : 'completed'
      step.duration = typeof event.duration === 'number' ? event.duration : null
    } else if (event.type === 'node_retry') {
      step.status = 'in-progress'
    }
    step.details = [...step.details, ...detailsFor(event)]
  }
  return {
    status: phase.status === 'waiting_input' ? 'waiting_input' : phase.status === 'failed' ? 'failed' : phase.status === 'canceled' ? 'canceled' : phase.status === 'completed' ? 'completed' : 'active',
    startedAt: Date.now() - phase.elapsedSeconds * 1000,
    elapsedSeconds: phase.elapsedSeconds,
    steps,
    stepOrder: order,
    pendingStepEvents: {},
    draftStreamId: null,
    draftText: '',
    finalResult: null,
    errorMessage: null,
    waitingInput: waitingForPhase(phase),
  }
}

/*
 * 一条用户消息只展示属于它自己的执行记录：
 * - 追问固定下来的实时记录优先；
 * - 答案已经作为独立消息存进历史时，历史阶段按文本展示，运行态记录不再重复同一段内容；
 * - 其余情况由仍代表这条输入的运行态记录继续推进，例如刷新页面后继续接收答案；
 * - 没有历史阶段的消息只有本页刚发送的那一条用户消息，运行态记录挂在它下面。
 */
function thinkingForMessage(message: ChatMessage, context: { liveOwnerLocalId: string | undefined; answerInHistory: boolean }): ThinkingProjection | null {
  if (message.frozenThinking) return message.frozenThinking
  const job = message.analysisJobId ? jobs.byId(message.analysisJobId) : undefined
  const phase = message.thinkingAfter
  if (phase) {
    if (context.answerInHistory) return phaseThinking(phase)
    if (!job || job.phaseInputId === null || job.phaseInputId !== phase.analysisJobInputId) return phaseThinking(phase)
    return job.thinking
  }
  return message.sender === 'user' && context.liveOwnerLocalId === message.localId && job ? job.thinking : null
}

/* 每个 Job 只有一个运行态记录，它的宿主是本页最后一条尚未固定执行记录的提问消息。 */
const liveOwnerByJob = computed(() => {
  const owners = new Map<string, string>()
  for (const message of props.messages) {
    if (message.sender !== 'user' || !message.analysisJobId || message.frozenThinking) continue
    owners.set(message.analysisJobId, message.localId)
  }
  return owners
})

/* 答案落到独立消息上之后，同一条输入的提问消息不再复用运行态记录展示同一段内容。 */
const answeredInputs = computed(() => {
  const keys = new Set<string>()
  for (const message of props.messages) {
    if (message.sender !== 'ai' || !message.analysisJobId) continue
    keys.add(`${message.analysisJobId}:${message.analysisJobInputId ?? ''}`)
  }
  return keys
})

const currentInteraction = computed(() => {
  const job = props.activeJob
  const waiting = job?.thinking.waitingInput
  const identity = job ? interactionIdentity(job) : null
  return job && waiting && identity && waiting.status === 'waiting'
    && (job.uiState === 'waiting_input' || job.uiState === 'resuming')
    ? { waiting, identity, job } : null
})

function promptText(message: ChatMessage): string | null {
  if (message.sender !== 'ai') return null
  return typeof message.text === 'string' ? message.text
    : message.text.type === 'human_input_required' ? message.text.summary ?? '' : null
}

/* 活动提示只搬动当前输入的最后一条匹配消息；旧问题即使正文相同也保留。 */
const currentPromptId = computed(() => {
  const current = currentInteraction.value
  if (!current) return null
  return [...props.messages].reverse().find((message) =>
    message.analysisJobId === current.identity.jobId
    && message.analysisJobInputId === (current.job.phaseInputId ?? undefined)
    && promptText(message) === current.waiting.prompt)?.localId ?? null
})

function historicalWaiting(message: ChatMessage, thinking: ThinkingProjection | null, index: number): WaitingInput | null {
  const waiting = thinking?.waitingInput
  if (!waiting) return null
  const current = currentInteraction.value
  if (current && message.analysisJobId === current.identity.jobId
    && waiting.questionId === current.identity.questionId && waiting.planVersion === current.identity.planVersion) return null
  if (waiting.status !== 'waiting') return waiting
  const answered = props.messages.slice(index + 1).some((next) => next.sender === 'user' && next.analysisJobId === message.analysisJobId)
  return { ...waiting, status: answered ? 'answered' : 'closed' }
}

function hasPersistedPrompt(message: ChatMessage, waiting: WaitingInput): boolean {
  return props.messages.some((other) => other.analysisJobId === message.analysisJobId
    && other.analysisJobInputId === message.analysisJobInputId && promptText(other) === waiting.prompt)
}

function historicalPromptStatus(message: ChatMessage, index: number): string | null {
  if (typeof message.text === 'string' || message.text.type !== 'human_input_required') return null
  return props.messages.slice(index + 1).some((next) => next.sender === 'user' && next.analysisJobId === message.analysisJobId)
    ? '已回答' : '已结束'
}

const messageRows = computed(() => props.messages.map((message, index) => ({
  message,
  index,
  thinking: thinkingForMessage(message, {
    liveOwnerLocalId: message.analysisJobId ? liveOwnerByJob.value.get(message.analysisJobId) : undefined,
    answerInHistory: Boolean(message.analysisJobId)
      && answeredInputs.value.has(`${message.analysisJobId}:${message.thinkingAfter?.analysisJobInputId ?? ''}`),
  }),
})).filter((row) => row.message.localId !== currentPromptId.value)
  .map((row) => ({ ...row, waiting: historicalWaiting(row.message, row.thinking, row.index) })))

watch(() => props.messages.length, () => scrollFollow.keepLatest())
watch(() => currentInteraction.value?.waiting, () => scrollFollow.keepLatest())
</script>

<template>
  <div v-if="!messageRows.length" class="conversation-empty" aria-live="polite">CausalAgent</div>
  <div v-for="row in messageRows" :key="row.message.localId" class="message-row" :class="`${row.message.sender}-message-row`">
    <article class="message" :class="`${row.message.sender}-message`">
      <div v-if="row.message.sender === 'user' && row.message.fileAttachment" class="selected-file-card message-file-card">
        <span class="selected-file-icon" aria-hidden="true">{{ fileExtension(row.message.fileAttachment.filename) }}</span>
        <span class="selected-file-details">
          <strong class="selected-file-name" :title="row.message.fileAttachment.filename">{{ row.message.fileAttachment.filename }}</strong>
        </span>
      </div>
      <MessageBody :text="row.message.text" />
    </article>
    <p v-if="historicalPromptStatus(row.message, row.index)" class="processing-options-status">{{ historicalPromptStatus(row.message, row.index) }}</p>
    <ThinkingTimeline
      v-if="row.thinking"
      :thinking="row.thinking"
      @decision-settled="onDecisionSettled(row.message.analysisJobId, $event)"
    />
    <StreamingDraft v-if="row.thinking?.draftText" :text="row.thinking.draftText" :animate="isPresentationActive(row.thinking.status)" />
    <article v-if="row.thinking?.finalResult" class="message ai-message">
      <MessageBody :text="row.thinking.finalResult" />
    </article>
    <article v-if="row.waiting" class="message ai-message interaction-message">
      <MessageBody v-if="!hasPersistedPrompt(row.message, row.waiting)" :text="{ type: 'human_input_required', summary: row.waiting.prompt }" />
      <ProcessingOptions
        :waiting="row.waiting"
        :identity="{ jobId: row.message.analysisJobId ?? '', questionId: row.waiting.questionId, planVersion: row.waiting.planVersion }"
      />
    </article>
    <article v-if="row.thinking?.errorMessage" class="message ai-message">
      <MessageBody :text="{ type: 'text', summary: `错误：${row.thinking.errorMessage}` }" />
    </article>
  </div>
  <article v-if="currentInteraction" class="message ai-message interaction-message current-interaction" aria-label="当前待回答问题">
    <MessageBody :text="{ type: 'human_input_required', summary: currentInteraction.waiting.prompt }" />
    <ProcessingOptions
      :waiting="currentInteraction.waiting"
      :identity="currentInteraction.identity"
      :submitting="interactionSubmitting || Boolean(props.submittingOptionId)"
      :error-message="interactionError"
      @choose="emit('choose-processing-option', $event)"
      @other="emit('use-other-requirement', $event)"
    />
  </article>
</template>
