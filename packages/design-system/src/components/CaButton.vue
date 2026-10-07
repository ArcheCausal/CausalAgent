<script setup lang="ts">
import { computed } from 'vue'
import type { CaButtonSize, CaButtonVariant } from '../types'

const props = withDefaults(
  defineProps<{
    variant?: CaButtonVariant
    size?: CaButtonSize
    type?: 'button' | 'submit' | 'reset'
    href?: string
    disabled?: boolean
    loading?: boolean
  }>(),
  {
    variant: 'primary',
    size: 'md',
    type: 'button',
    href: undefined,
    disabled: false,
    loading: false,
  },
)

const emit = defineEmits<{ (event: 'click', payload: MouseEvent): void }>()

const isLink = computed(() => props.href !== undefined)
const inactive = computed(() => props.disabled || props.loading)

const classes = computed(() => [
  'ca-btn',
  `ca-btn--${props.variant}`,
  `ca-btn--${props.size}`,
  { 'is-loading': props.loading },
])

function onClick(event: MouseEvent) {
  if (inactive.value) {
    event.preventDefault()
    event.stopPropagation()
    return
  }
  emit('click', event)
}
</script>

<template>
  <component
    :is="isLink ? 'a' : 'button'"
    :class="classes"
    :href="isLink ? props.href : undefined"
    :type="isLink ? undefined : props.type"
    :disabled="!isLink && inactive ? true : undefined"
    :aria-disabled="inactive ? 'true' : undefined"
    :aria-busy="props.loading ? 'true' : undefined"
    @click="onClick"
  >
    <span class="ca-btn__content">
      <span v-if="$slots.icon" class="ca-btn__icon"><slot name="icon" /></span>
      <slot />
    </span>
    <span v-if="props.loading" class="ca-btn__progress" aria-hidden="true"><i /></span>
    <span v-if="props.loading" class="ca-visually-hidden">加载中</span>
  </component>
</template>
