<script setup lang="ts">
import {
  CaBadge,
  CaButton,
  CaCard,
  CaEmptyState,
  CaErrorState,
  CaPageHeader,
} from '@causalagent/design-system'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ApiError, adminApi } from '../api'
import { formatDate, statusLabel } from '../lib/dashboard'
import { statusTone } from '../lib/statusTone'
import type { DeepAuditSnapshot, QuickAuditCheck, QuickAuditSnapshot } from '../types'

const quick = ref<QuickAuditSnapshot | null>(null)
const deep = ref<DeepAuditSnapshot | null>(null)
const loading = ref(true)
const running = ref(false)
const error = ref('')
const notice = ref('')
let pollTimer: number | undefined
let requestedAt = 0
let deadline = 0

const quickChecks = computed<QuickAuditCheck[]>(() => quick.value?.checks || [])

/** 读取 quick 和最近一次 deep 共享审计快照。 */
async function loadAudits(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const [quickResult, deepResult] = await Promise.all([
      adminApi.quickAudit(),
      adminApi.deepAudit(),
    ])
    quick.value = quickResult
    deep.value = deepResult
  } catch (caught) {
    showError(caught)
  } finally {
    loading.value = false
  }
}

/** 显示带 request ID 的审计页面错误。 */
function showError(caught: unknown): void {
  const apiError = caught as ApiError
  error.value = `${apiError.message}（请求 ID：${apiError.requestId || '未知'}）`
}

/** 判断 deep 快照是否已经覆盖本次手动请求。 */
function deepRequestCompleted(snapshot: DeepAuditSnapshot): boolean {
  const observed = Date.parse(snapshot.observed_at || '')
  return !snapshot.refresh_pending && Number.isFinite(observed) && observed >= requestedAt
}

/** 轮询共享快照，直到 monitor 完成本次 deep 请求或超时。 */
async function pollDeep(): Promise<void> {
  try {
    const snapshot = await adminApi.deepAudit()
    deep.value = snapshot
    if (deepRequestCompleted(snapshot)) {
      running.value = false
      notice.value = 'deep 审计已由 monitor 完成。'
      return
    }
    if (Date.now() >= deadline) {
      running.value = false
      error.value = 'deep 审计仍在排队，请稍后重新读取；Web 请求未执行审计。'
      return
    }
    pollTimer = window.setTimeout(pollDeep, 1500)
  } catch (caught) {
    running.value = false
    showError(caught)
  }
}

/** 登记手动 deep 审计请求并开始轮询共享结果。 */
async function runDeep(): Promise<void> {
  running.value = true
  error.value = ''
  notice.value = ''
  if (pollTimer !== undefined) window.clearTimeout(pollTimer)
  try {
    const requested = await adminApi.runDeepAudit()
    requestedAt = Date.parse(requested.requested_at)
    deadline = Date.now() + 60_000
    await pollDeep()
  } catch (caught) {
    running.value = false
    showError(caught)
  }
}

/** 把 deep 检查详情稳定格式化为只读 JSON。 */
function formatDetails(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2)
}

/** 组合 Quick 检查目的与本次异常原因，并兼容旧共享快照。 */
function quickCheckDescription(check: QuickAuditCheck): string {
  const description = check.description?.trim() || ''
  const warning = check.warning?.trim() || ''
  if (description && warning && description !== warning) {
    return `${description}；当前结果：${warning}`
  }
  return description || warning || '—'
}

onMounted(loadAudits)
onBeforeUnmount(() => {
  if (pollTimer !== undefined) window.clearTimeout(pollTimer)
})
</script>

<template>
  <section class="admin-page">
    <CaPageHeader title="Schema与审计" :level="1" size="md">
      <template #actions>
        <CaButton variant="secondary" :loading="loading" @click="loadAudits">重新读取</CaButton>
        <CaButton variant="primary" :loading="running" @click="runDeep">运行 deep 审计</CaButton>
      </template>
    </CaPageHeader>

    <CaErrorState v-if="error" class="page-notice" title="审计读取失败" :description="error" />
    <el-alert v-if="notice" class="page-notice" type="success" :closable="false" :title="notice" />

    <CaCard as="section" class="panel" variant="outline" padding="md">
      <div class="panel-header">
        <div>
          <h2>Quick 完整性</h2>
        </div>
        <span class="source-meta">{{ formatDate(quick?.observed_at) }}</span>
      </div>
      <el-table v-loading="loading" :data="quickChecks" empty-text="尚无 quick 审计结果">
        <el-table-column prop="label" label="检查" min-width="220" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <CaBadge :tone="statusTone(row.status)">
              {{ row.status }}
            </CaBadge>
          </template>
        </el-table-column>
        <el-table-column label="说明" min-width="280" show-overflow-tooltip>
          <template #default="{ row }">{{ quickCheckDescription(row) }}</template>
        </el-table-column>
      </el-table>
    </CaCard>

    <CaCard as="section" class="panel" variant="outline" padding="md">
      <div class="panel-header">
        <div>
          <h2>Deep 审计</h2>
        </div>
        <div class="source-meta">
          <CaBadge :tone="statusTone(deep?.status)">
            {{ statusLabel(deep?.status || 'unknown') }}
          </CaBadge>
          <span>{{ formatDate(deep?.observed_at) }}</span>
        </div>
      </div>

      <el-alert
        v-if="deep?.refresh_pending"
        class="page-notice"
        type="info"
        :closable="false"
        title="deep 审计请求已登记，正在等待 monitor 处理。"
      />

      <el-collapse v-loading="loading || running">
        <el-collapse-item
          v-for="check in deep?.checks || []"
          :key="check.key"
          :name="check.key"
        >
          <template #title>
            <div class="audit-check-title">
              <CaBadge :tone="statusTone(check.status)">
                {{ statusLabel(check.status) }}
              </CaBadge>
              <strong>{{ check.label }}</strong>
              <span>{{ check.summary }}</span>
            </div>
          </template>
          <pre class="audit-details" v-text="formatDetails(check.details)" />
        </el-collapse-item>
      </el-collapse>
      <CaEmptyState v-if="!loading && !(deep?.checks || []).length" description="尚未运行 deep 审计" />
    </CaCard>
  </section>
</template>
