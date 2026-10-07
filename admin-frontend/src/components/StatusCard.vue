<script setup lang="ts">
import { computed } from 'vue'
import { CaBadge, CaCard } from '@causalagent/design-system'
import { displayStatus, metaText, statusLabel } from '../lib/dashboard'
import { statusTone } from '../lib/statusTone'
import type { SnapshotMeta } from '../types'

const props = defineProps<{
  label: string
  value: string | number
  detail: string
  meta: SnapshotMeta
}>()

const status = computed(() => displayStatus(props.meta))
const completeMetaText = computed(() => metaText(props.meta))
</script>

<template>
  <CaCard
    as="article"
    class="status-card"
    :class="`status-${status}`"
    :data-status="status"
    variant="outline"
    padding="sm"
  >
    <div class="card-heading">
      <span>{{ label }}</span>
      <CaBadge :tone="statusTone(status)">
        {{ statusLabel(status) }}
      </CaBadge>
    </div>
    <strong class="card-value">{{ value }}</strong>
    <p class="card-detail">{{ detail || meta.warning || '暂无补充信息' }}</p>
    <small class="card-meta" :title="completeMetaText">{{ completeMetaText }}</small>
  </CaCard>
</template>
