<script setup lang="ts">
import { CaButton } from '@causalagent/design-system'

defineProps<{
  canPrevious: boolean
  hasMore: boolean
  loading?: boolean
}>()

defineEmits<{
  previous: []
  next: []
}>()
</script>

<template>
  <div class="cursor-pager">
    <span>每页最多展示20条</span>
    <div>
      <CaButton size="sm" variant="secondary" :disabled="!canPrevious || loading" @click="$emit('previous')">
        上一页
      </CaButton>
      <CaButton size="sm" variant="primary" :disabled="!hasMore || loading" @click="$emit('next')">
        下一页
      </CaButton>
    </div>
  </div>
</template>
