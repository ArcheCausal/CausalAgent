<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  PRESENTATION_CHARS_PER_TICK,
  PRESENTATION_TICK_MS,
  prefersReducedMotion,
  textLength,
} from '../runtime/chat/presentation'
import { useChatScrollFollow } from '../runtime/chat/scroll-follow'
import { decisionKindPrefix } from '../runtime/jobs/step-details'
import type { StepDecisionDetail, StepDetail, ThinkingStep } from '../types/domain'

const props = defineProps<{ step: ThinkingStep; animate: boolean }>()
const emit = defineEmits<{ 'decision-settled': [{ stepId: string; key: string }] }>()
const scrollFollow = useChatScrollFollow()
const revealed = ref<Record<string, number>>({})
let timer: number | null = null

const decisions = computed(() => props.step.details.filter(
  (detail): detail is StepDecisionDetail => detail.kind === 'decision',
))

function stopTimer(): void {
  if (timer !== null) {
    globalThis.clearInterval(timer)
    timer = null
  }
}

// 同一阶段内按到达顺序串行推进，避免多条公开决策同时逐字出现。
function nextEntry(): StepDecisionDetail | null {
  if (!props.animate || prefersReducedMotion()) return null
  for (const entry of decisions.value) {
    if (entry.released) continue
    if ((revealed.value[entry.key] ?? 0) < textLength(entry.text)) return entry
  }
  return null
}

function visibleText(entry: StepDecisionDetail): string {
  if (!props.animate || prefersReducedMotion() || entry.released) return entry.text
  return Array.from(entry.text).slice(0, revealed.value[entry.key] ?? 0).join('')
}

function revealedLength(entry: StepDecisionDetail): number {
  if (!props.animate || prefersReducedMotion()) return textLength(entry.text)
  return revealed.value[entry.key] ?? 0
}

/** 展示追平后通知上层放行被挂起的工具事件；未收到完整 decision 时不放行。 */
function settleDecisions(): void {
  for (const entry of decisions.value) {
    if (entry.released || !entry.complete) continue
    if (revealedLength(entry) >= textLength(entry.text)) {
      emit('decision-settled', { stepId: props.step.stepId, key: entry.key })
    }
  }
}

function tick(): void {
  const entry = nextEntry()
  if (entry) {
    const current = revealed.value[entry.key] ?? 0
    const limit = textLength(entry.text)
    revealed.value = { ...revealed.value, [entry.key]: Math.min(limit, current + PRESENTATION_CHARS_PER_TICK) }
    scrollFollow.keepLatest()
  } else {
    stopTimer()
  }
  settleDecisions()
}

function sync(): void {
  if (nextEntry()) {
    if (timer === null) timer = globalThis.setInterval(tick, PRESENTATION_TICK_MS)
  } else {
    stopTimer()
  }
  settleDecisions()
}

function detailKey(detail: StepDetail, index: number): string {
  return detail.kind === 'decision' ? `${index}-${detail.key}` : `${index}-${detail.text}`
}

watch(() => props.step.details, sync)
watch(() => props.animate, sync)

onMounted(sync)
onBeforeUnmount(stopTimer)
</script>

<template>
  <div class="step-details">
    <template v-for="(detail, index) in step.details" :key="detailKey(detail, index)">
      <p v-if="detail.kind === 'text'" class="step-detail" :class="{ error: detail.tone === 'error', retry: detail.tone === 'retry' }">{{ detail.text }}</p>
      <p v-else class="step-detail decision-detail">{{ decisionKindPrefix(detail.decisionKind) }}{{ visibleText(detail) }}</p>
    </template>
  </div>
</template>
