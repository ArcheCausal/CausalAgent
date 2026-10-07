<script setup lang="ts">
import { computed } from 'vue'
import type { CaLoadingStateVariant } from '../types'

const props = withDefaults(
  defineProps<{
    variant?: CaLoadingStateVariant
    lines?: number
    showTitle?: boolean
    value?: number
    label?: string
  }>(),
  {
    variant: 'skeleton',
    lines: 3,
    showTitle: true,
    value: undefined,
    label: '加载中',
  },
)

const isDeterminate = computed(() => props.variant === 'line' && props.value !== undefined)

const ruleCount = computed(() => Math.max(props.lines, 1))

const progressPercent = computed(() => Math.round(Math.min(Math.max(props.value ?? 0, 0), 1) * 100))

const rootRole = computed(() => (isDeterminate.value ? 'progressbar' : 'status'))
</script>

<template>
  <div
    :class="['ca-loading', `ca-loading--${props.variant}`]"
    :role="rootRole"
    :aria-busy="isDeterminate ? undefined : 'true'"
    :aria-valuemin="isDeterminate ? 0 : undefined"
    :aria-valuemax="isDeterminate ? 100 : undefined"
    :aria-valuenow="isDeterminate ? progressPercent : undefined"
  >
    <template v-if="props.variant === 'skeleton'">
      <span v-if="props.showTitle" class="ca-loading__rule ca-loading__rule--title" />
      <span
        v-for="index in ruleCount"
        :key="index"
        :class="['ca-loading__rule', { 'ca-loading__rule--short': ruleCount > 1 && index === ruleCount }]"
      />
    </template>
    <span v-else class="ca-loading__track">
      <span
        :class="['ca-loading__fill', { 'ca-loading__fill--indeterminate': !isDeterminate }]"
        :style="isDeterminate ? { transform: `scaleX(${progressPercent / 100})` } : undefined"
      />
    </span>
    <span class="ca-visually-hidden">{{ props.label }}</span>
  </div>
</template>
