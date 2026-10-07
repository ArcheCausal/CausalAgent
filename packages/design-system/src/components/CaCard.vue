<script setup lang="ts">
import type { CaCardPadding, CaCardVariant } from '../types'

const props = withDefaults(
  defineProps<{
    variant?: CaCardVariant
    padding?: CaCardPadding
    clip?: boolean
    as?: 'div' | 'section' | 'article' | 'aside' | 'li'
  }>(),
  {
    variant: 'outline',
    padding: 'md',
    clip: false,
    as: 'div',
  },
)
</script>

<template>
  <component
    :is="props.as"
    :class="[
      'ca-card',
      `ca-card--${props.variant}`,
      `ca-card--pad-${props.padding}`,
      { 'ca-card--clip': props.clip },
    ]"
  >
    <slot />
  </component>
</template>
