<script setup lang="ts">
import { computed } from 'vue'
import type { CaPageHeaderSize } from '../types'

const props = withDefaults(
  defineProps<{
    title: string
    description?: string
    level?: 1 | 2 | 3 | 4 | 5 | 6
    size?: CaPageHeaderSize
    divider?: boolean
  }>(),
  {
    description: undefined,
    level: 2,
    size: 'md',
    divider: false,
  },
)

const headings = { 1: 'h1', 2: 'h2', 3: 'h3', 4: 'h4', 5: 'h5', 6: 'h6' } as const

const heading = computed(() => headings[props.level])
</script>

<template>
  <header
    :class="['ca-page-header', `ca-page-header--${props.size}`, { 'ca-page-header--divider': props.divider }]"
  >
    <div class="ca-page-header__row">
      <component :is="heading" class="ca-page-header__title">{{ props.title }}</component>
      <slot />
      <div v-if="$slots.actions" class="ca-page-header__actions"><slot name="actions" /></div>
    </div>
    <p v-if="props.description" class="ca-page-header__description">{{ props.description }}</p>
  </header>
</template>
