<script setup lang="ts">
import type { CaEmptyStateAlign } from '../types'

const props = withDefaults(
  defineProps<{
    title?: string
    description?: string
    align?: CaEmptyStateAlign
  }>(),
  {
    title: undefined,
    description: undefined,
    align: 'center',
  },
)
</script>

<template>
  <div :class="['ca-empty', `ca-empty--${props.align}`]">
    <span v-if="$slots.icon" class="ca-empty__icon"><slot name="icon" /></span>
    <p v-if="props.title" class="ca-empty__title">{{ props.title }}</p>
    <p v-if="props.description" class="ca-empty__description">{{ props.description }}</p>
    <slot />
    <div v-if="$slots.actions" class="ca-empty__actions"><slot name="actions" /></div>
  </div>
</template>
