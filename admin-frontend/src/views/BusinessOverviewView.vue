<script setup lang="ts">
import {
  CaBadge,
  CaButton,
  CaCard,
  CaErrorState,
  CaPageHeader,
} from '@causalagent/design-system'
import { onMounted, ref } from 'vue'
import { ApiError, adminApi } from '../api'
import { formatDate, formatNumber, statusLabel } from '../lib/dashboard'
import { statusTone } from '../lib/statusTone'
import type { BusinessOverview } from '../types'

const overview = ref<BusinessOverview | null>(null)
const loading = ref(true)
const error = ref('')

/** 从 Flask 读取业务估算指标和共享快照摘要。 */
async function loadOverview(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    overview.value = await adminApi.businessOverview()
  } catch (caught) {
    const apiError = caught as ApiError
    error.value = `${apiError.message}（请求 ID：${apiError.requestId || '未知'}）`
  } finally {
    loading.value = false
  }
}

onMounted(loadOverview)
</script>

<template>
  <section class="admin-page">
    <CaPageHeader title="业务概览" :level="1" size="md">
      <template #actions>
        <CaButton variant="secondary" :loading="loading" @click="loadOverview">重新读取</CaButton>
      </template>
    </CaPageHeader>

    <CaErrorState v-if="error" class="page-notice" title="读取业务概览失败" :description="error" />

    <div v-loading="loading" class="overview-grid">
      <CaCard
        v-for="metric in overview?.metrics || []"
        :key="metric.key"
        as="article"
        class="overview-card"
        variant="outline"
        padding="sm"
      >
        <span>{{ metric.label }}</span>
        <strong>{{ formatNumber(metric.value) }}</strong>
        <small>{{ metric.is_estimate ? '估算' : '精确' }} · {{ metric.source_alias }}</small>
      </CaCard>
    </div>

    <CaCard as="section" class="panel" variant="outline" padding="md">
      <div class="panel-header">
        <div>
          <h2>共享监控快照</h2>
        </div>
        <span class="source-meta">
          统计时间 {{ formatDate(overview?.observed_at) }}
        </span>
      </div>
      <el-table v-loading="loading" :data="overview?.snapshots || []" empty-text="暂无共享快照">
        <el-table-column prop="snapshot_key" label="快照类型" min-width="160" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <CaBadge :tone="statusTone(row.status)">
              {{ statusLabel(row.status) }}
            </CaBadge>
          </template>
        </el-table-column>
        <el-table-column label="时间" min-width="190">
          <template #default="{ row }">{{ formatDate(row.observed_at) }}</template>
        </el-table-column>
        <el-table-column label="刷新请求" min-width="190">
          <template #default="{ row }">{{ formatDate(row.refresh_requested_at) }}</template>
        </el-table-column>
        <el-table-column prop="source_alias" label="来源" min-width="180" />
        <el-table-column prop="warning" label="说明" min-width="220" show-overflow-tooltip />
      </el-table>
    </CaCard>
  </section>
</template>
