<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useLocale } from '../i18n/use-locale'
import { isPresentationActive } from '../runtime/chat/presentation'
import { useChatScrollFollow } from '../runtime/chat/scroll-follow'
import { stepDetailText, textDetail } from '../runtime/jobs/step-details'
import { useJobsStore } from '../stores/jobs.store'
import type { ChatMessage, ExecutionPhase, StepDetail, ThinkingProjection } from '../types/domain'
import MessageBody from './MessageBody.vue'
import StreamingDraft from './StreamingDraft.vue'
import ThinkingTimeline from './ThinkingTimeline.vue'

const props = defineProps<{
  messages: ChatMessage[]
  editsEnabled?: boolean
  canCancelJob?: boolean
  editing?: {
    messageId: number
    value: string
    busy: boolean
    error: string
    reanalysisRequired: boolean
    needsRefresh: boolean
  } | null
}>()
const emit = defineEmits<{
  'copy-result': [success: boolean]
  edit: [message: ChatMessage]
  'edit-input': [value: string]
  'cancel-edit': []
  resend: [reanalyze: boolean]
  'refresh-edit': []
  'cancel-job': []
}>()
const jobs = useJobsStore()
const scrollFollow = useChatScrollFollow()
const { text } = useLocale()
const timeline = ref<HTMLElement | null>(null)
let composing = false

watch(() => props.editing?.messageId, async (id, previous) => {
  composing = false
  await nextTick()
  if (id !== undefined) timeline.value?.querySelector('textarea')?.focus({ preventScroll: true })
  else if (previous !== undefined) {
    timeline.value?.querySelector<HTMLButtonElement>(`[data-message-id="${previous}"] .edit-message-button`)?.focus({ preventScroll: true })
  }
})

function editKeydown(event: KeyboardEvent): void {
  if (composing || event.isComposing || event.keyCode === 229) return
  if (event.key === 'Escape' && !props.editing?.busy) {
    event.preventDefault()
    emit('cancel-edit')
  } else if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    if (!props.editing?.busy && !props.editing?.needsRefresh && !props.editing?.reanalysisRequired) emit('resend', false)
  }
}

function startComposition(): void {
  composing = true
}

function endComposition(): void {
  composing = false
}

function onDecisionSettled(jobId: string | undefined, payload: { stepId: string; key: string }): void {
  if (!jobId) return
  jobs.settleDecision(jobId, payload.stepId, payload.key)
}

function fileExtension(filename: string): string {
  const extension = filename.split('.').pop()?.trim()
  return extension ? extension.toUpperCase() : 'FILE'
}

/** 只有已持久化的历史阶段事件会走到这里；公开决策在历史中本来就是完整文本。 */
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
    waitingInput: null,
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

const messageRows = computed(() => props.messages.map((message) => ({
  message,
  thinking: thinkingForMessage(message, {
    liveOwnerLocalId: message.analysisJobId ? liveOwnerByJob.value.get(message.analysisJobId) : undefined,
    answerInHistory: Boolean(message.analysisJobId)
      && answeredInputs.value.has(`${message.analysisJobId}:${message.thinkingAfter?.analysisJobInputId ?? ''}`),
  }),
})))

watch(() => props.messages.length, () => scrollFollow.keepLatest())
</script>

<template>
  <div ref="timeline" class="message-timeline">
    <div v-if="!messageRows.length" class="conversation-empty" aria-live="polite">CausalAgent</div>
    <div v-for="(row, index) in messageRows" :key="row.message.localId" class="message-row" :class="`${row.message.sender}-message-row`" :data-message-id="row.message.messageId">
      <article class="message" :class="`${row.message.sender}-message`">
        <div v-if="row.message.sender === 'user' && row.message.fileAttachment" class="selected-file-card message-file-card">
          <span class="selected-file-icon" aria-hidden="true">{{ fileExtension(row.message.fileAttachment.filename) }}</span>
          <span class="selected-file-details">
            <strong class="selected-file-name" :title="row.message.fileAttachment.filename">{{ row.message.fileAttachment.filename }}</strong>
          </span>
        </div>
        <form v-if="editing && editing.messageId === row.message.messageId" class="message-edit-form" @submit.prevent="emit('resend', false)">
          <textarea
            :value="editing.value"
            :aria-label="text.editMessage"
            :readonly="editing.busy"
            rows="4"
            @input="emit('edit-input', ($event.target as HTMLTextAreaElement).value)"
            @compositionstart="startComposition"
            @compositionend="endComposition"
            @keydown="editKeydown"
          ></textarea>
          <p v-if="index < messageRows.length - 1 || row.thinking?.finalResult || row.thinking?.waitingInput">
            {{ text.replaceFollowing }}
          </p>
          <p v-if="editing.reanalysisRequired">{{ text.reanalyzeNotice }}</p>
          <p v-if="editing.error" class="message-edit-error" role="alert">{{ editing.error }}</p>
          <div class="message-edit-actions">
            <button type="button" class="secondary-button" :disabled="editing.busy" @click="emit('cancel-edit')">{{ text.cancel }}</button>
            <button v-if="canCancelJob" type="button" class="secondary-button" :disabled="editing.busy" @click="emit('cancel-job')">{{ text.cancelJob }}</button>
            <button v-if="editing.needsRefresh" type="button" class="secondary-button" :disabled="editing.busy" @click="emit('refresh-edit')">{{ text.refreshEditHistory }}</button>
            <button v-else-if="editing.reanalysisRequired" type="button" class="secondary-button" :disabled="editing.busy || !editing.value.trim()" @click="emit('resend', true)">{{ text.reanalyzeMessage }}</button>
            <button v-else type="submit" class="secondary-button" :disabled="editing.busy || !editing.value.trim()">{{ editing.busy ? text.editSubmitting : text.resendMessage }}</button>
          </div>
        </form>
        <MessageBody
          v-else
          :text="row.message.text"
          :editable="editsEnabled && row.message.sender === 'user' && typeof row.message.text === 'string' && row.message.messageId !== undefined"
          @copy-result="emit('copy-result', $event)"
          @edit="emit('edit', row.message)"
        />
      </article>
      <ThinkingTimeline
        v-if="row.thinking"
        :thinking="row.thinking"
        @decision-settled="onDecisionSettled(row.message.analysisJobId, $event)"
      />
      <StreamingDraft
        v-if="row.thinking?.draftText"
        :text="row.thinking.draftText"
        :animate="isPresentationActive(row.thinking.status)"
        @copy-result="emit('copy-result', $event)"
      />
      <article v-if="row.thinking?.finalResult" class="message ai-message">
        <MessageBody :text="row.thinking.finalResult" @copy-result="emit('copy-result', $event)" />
      </article>
      <article v-if="row.thinking?.waitingInput" class="message ai-message">
        <MessageBody :text="{ type: 'human_input_required', summary: row.thinking.waitingInput.prompt }" @copy-result="emit('copy-result', $event)" />
      </article>
      <article v-if="row.thinking?.errorMessage" class="message ai-message">
        <MessageBody :text="{ type: 'text', summary: `错误：${row.thinking.errorMessage}` }" @copy-result="emit('copy-result', $event)" />
      </article>
    </div>
  </div>
</template>
