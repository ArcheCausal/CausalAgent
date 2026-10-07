<script setup lang="ts">
import type { CaBadgeTone } from '../types'

const props = withDefaults(defineProps<{ tone?: CaBadgeTone }>(), { tone: 'neutral' })
</script>

<template>
  <span :class="['ca-badge', `ca-badge--${props.tone}`]"><slot /></span>
</template>
