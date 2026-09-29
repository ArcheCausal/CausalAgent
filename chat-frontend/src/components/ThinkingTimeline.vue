<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useLocale } from '../i18n/use-locale'
import { isPresentationActive } from '../runtime/chat/presentation'
import { useChatScrollFollow } from '../runtime/chat/scroll-follow'
import type { ThinkingProjection, ThinkingStep } from '../types/domain'
import ThinkingStepDetails from './ThinkingStepDetails.vue'

const props = defineProps<{ thinking: ThinkingProjection }>()
const emit = defineEmits<{ 'decision-settled': [{ stepId: string; key: string }] }>()
const { text } = useLocale()
const scrollFollow = useChatScrollFollow()
const expanded = ref(true)
const expandedSteps = ref<Record<string, boolean>>({})
const now = ref(Date.now())
let durationTimer: number | null = null

const animate = computed(() => isPresentationActive(props.thinking.status))
const stepRows = computed<ThinkingStep[]>(() => props.thinking.stepOrder
  .map((stepId) => props.thinking.steps[stepId])
  .filter((step): step is ThinkingStep => Boolean(step)))

const elapsed = computed(() => props.thinking.status === 'active'
  ? Math.max(0, now.value - props.thinking.startedAt) / 1000
  : props.thinking.elapsedSeconds)

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(2).replace(/0+$/, '').replace(/\.$/, '') || '0'}s`
  const whole = Math.floor(seconds)
  const minutes = Math.floor(whole / 60)
  return `${minutes}m ${whole % 60}s`
}

function statusText(): string {
  if (props.thinking.status === 'waiting_input') return text.value.waitingInput
  if (props.thinking.status === 'failed') return text.value.taskFailed
  if (props.thinking.status === 'canceled') return text.value.taskCanceled
  return props.thinking.status === 'active' ? text.value.thinking : text.value.processed
}

function toggleStep(stepId: string): void {
  expandedSteps.value = { ...expandedSteps.value, [stepId]: !expandedSteps.value[stepId] }
}

onMounted(() => {
  durationTimer = globalThis.setInterval(() => { now.value = Date.now() }, 100)
  scrollFollow.keepLatest()
})

watch(() => stepRows.value.length, () => scrollFollow.keepLatest())

onBeforeUnmount(() => {
  if (durationTimer !== null) globalThis.clearInterval(durationTimer)
  durationTimer = null
})
</script>

<template>
  <div class="thinking-block thinking-bubble">
    <button class="thinking-header" type="button" :aria-expanded="expanded" @click="expanded = !expanded">
      <span>{{ statusText() }}</span>
      <span class="thinking-duration">{{ formatElapsed(elapsed) }}</span>
      <span v-if="thinking.status === 'active'" class="thinking-dots" aria-hidden="true">...</span>
      <span class="disclosure">{{ expanded ? '▾' : '▸' }}</span>
    </button>
    <div v-if="thinking.status === 'active'" class="think-line" aria-hidden="true"><i></i></div>
    <div v-show="expanded" class="thinking-detail-container">
      <div class="thinking-detail">
        <div v-for="step in stepRows" :key="step.stepId" class="thinking-step step-item" :class="`is-${step.status}`">
          <button class="step-header" type="button" @click="toggleStep(step.stepId)">
            <span class="step-status" aria-hidden="true"></span>
            <span class="step-name">{{ step.title }}</span>
            <span class="step-time">{{ step.duration !== null ? `${step.duration}s` : text.inProgress }}</span>
            <span class="disclosure">{{ expandedSteps[step.stepId] ? '▾' : '▸' }}</span>
          </button>
          <ThinkingStepDetails
            v-show="expandedSteps[step.stepId]"
            :step="step"
            :animate="animate"
            @decision-settled="emit('decision-settled', $event)"
          />
        </div>
      </div>
    </div>
  </div>
</template>
