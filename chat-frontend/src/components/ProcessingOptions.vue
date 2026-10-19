<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { WaitingInput } from '../types/domain'

const props = defineProps<{
  waiting: WaitingInput
  submitting?: boolean
  serverExpired?: boolean
}>()
const emit = defineEmits<{
  'choose': [optionId: string]
  'other': []
}>()

const now = ref(Date.now())
let timer: number | null = null

const remainingSeconds = computed(() => {
  if (!props.waiting.expiresAt) return null
  const deadline = Date.parse(props.waiting.expiresAt)
  if (!Number.isFinite(deadline)) return null
  return Math.max(0, Math.ceil((deadline - now.value) / 1000))
})
const expired = computed(() =>
  Boolean(props.serverExpired) || (remainingSeconds.value !== null && remainingSeconds.value <= 0),
)
const countdown = computed(() => {
  const seconds = remainingSeconds.value
  if (seconds === null) return ''
  const minutes = Math.floor(seconds / 60)
  const rest = String(seconds % 60).padStart(2, '0')
  return minutes + ':' + rest
})

watch(
  () => expired.value,
  (value) => {
    if (value && timer !== null) {
      window.clearInterval(timer)
      timer = null
    }
  },
  { immediate: true },
)

if (props.waiting.expiresAt) {
  timer = window.setInterval(() => {
    now.value = Date.now()
  }, 1000)
}
onBeforeUnmount(() => {
  if (timer !== null) window.clearInterval(timer)
})

function choose(optionId: string): void {
  if (expired.value || props.submitting) return
  emit('choose', optionId)
}
</script>

<template>
  <section class="processing-options" aria-label="数据处理方案确认">
    <p v-if="!expired" class="processing-options-hint">
      请选择一种处理方式；其他要求可在下方输入框说明。
      <span v-if="countdown" class="processing-options-countdown">剩余 {{ countdown }}</span>
    </p>
    <p v-else class="processing-options-expired">该确认已过期，当前任务不能继续提交。</p>
    <ul class="processing-option-list">
      <li
        v-for="option in waiting.options"
        :key="option.optionId"
        class="processing-option"
        :class="{ selected: waiting.selectedOptionId === option.optionId }"
      >
        <button
          type="button"
          class="processing-option-button"
          :disabled="expired || submitting"
          :aria-pressed="waiting.selectedOptionId === option.optionId"
          @click="choose(option.optionId)"
        >
          <strong>{{ option.label }}</strong>
          <span class="processing-option-algorithm">{{ option.capabilityId }}</span>
        </button>
        <ul v-if="option.steps.length" class="processing-option-steps">
          <li v-for="(step, index) in option.steps" :key="index">
            {{ step.operation }}：{{ step.columns.join('、') }}
            <template v-if="step.method">（{{ step.method }}）</template>
          </li>
        </ul>
        <ul v-if="option.impact.length" class="processing-option-notes">
          <li v-for="(item, index) in option.impact" :key="index">{{ item }}</li>
        </ul>
        <ul v-if="option.risks.length" class="processing-option-risks">
          <li v-for="(item, index) in option.risks" :key="index">{{ item }}</li>
        </ul>
      </li>
    </ul>
    <button
      v-if="waiting.allowOther"
      type="button"
      class="processing-option-other"
      :disabled="expired || submitting"
      @click="emit('other')"
    >
      其他要求
    </button>
  </section>
</template>
