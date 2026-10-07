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
import { useRoute, useRouter } from 'vue-router'
import { adminApi } from '../api'
import SqlDigestTable from '../components/SqlDigestTable.vue'
import StatusCard from '../components/StatusCard.vue'
import {
  MANUAL_POLL_INTERVAL_MS,
  MANUAL_REFRESH_TIMEOUT_MS,
  REFRESH_GROUP_KEYS,
  SUCCESS_NOTICE_DURATION_MS,
  deriveBlockingCard,
  displayStatus,
  displayValue,
  formatBytes,
  formatDate,
  formatNumber,
  isObject,
  metaText,
  metricResult,
  metricValue,
  snapshotObservedRequest,
  statusLabel,
} from '../lib/dashboard'
import { statusTone } from '../lib/statusTone'
import type { DashboardData, SnapshotMeta } from '../types'

type DashboardView = 'database' | 'cleanup-worker' | 'outbox'

const route = useRoute()
const router = useRouter()

const dashboard = ref<DashboardData | null>(null)
const loading = ref(true)
const pageNotice = ref('')
const noticeType = ref<'error' | 'warning' | 'info'>('error')
const refreshing = ref(false)
const auditing = ref(false)
let autoRefreshTimer: number | undefined
let noticeTimer: number | undefined
let dashboardRequest: Promise<DashboardData> | null = null

const activeView = computed<DashboardView>(() => {
  const value = String(route.query.view || 'database')
  return value === 'cleanup-worker' || value === 'outbox' ? value : 'database'
})
const operationId = computed(() => {
  const value = route.query.operation_id
  return typeof value === 'string' ? value : ''
})

const viewOptions: Array<{ label: string; value: DashboardView }> = [
  { label: '数据库运行状态', value: 'database' },
  { label: '持久化清理 Worker', value: 'cleanup-worker' },
  { label: 'Outbox 队列', value: 'outbox' },
]

const cleanupTaskTypeLabel = (value: unknown): string => (
  value === 'checkpoint' ? '父子图 checkpoint' : value === 'user_memory' ? '长期记忆 Store' : '—'
)

const failureMeta = computed<SnapshotMeta>(() => ({
  status: 'unknown',
  observed_at: null,
  source_alias: '共享监控快照',
  warning: pageNotice.value || '数据库看板尚未加载',
}))

/** 从聚合响应取得指定分层，并把未知值安全收敛为空元数据。 */
function group(name: keyof DashboardData): SnapshotMeta {
  const value = dashboard.value?.[name]
  return isObject(value) ? value as SnapshotMeta : {}
}

/** 把模板使用的未知嵌套值规范为普通对象。 */
function record(value: unknown): Record<string, unknown> {
  return isObject(value) ? value : {}
}

const realtime = computed(() => group('realtime'))
const capacity = computed(() => group('capacity'))
const sqlPerformance = computed(() => group('sql_performance'))
const integrity = computed(() => group('integrity'))
const cleanupRuntime = computed(() => record(dashboard.value?.agent_persistence_cleanup_runtime))
const cleanupOutbox = computed(() => record(dashboard.value?.agent_persistence_cleanup_outbox))
const cleanupSummary = computed(() => record(cleanupOutbox.value.summary))
const checkpointSummary = computed(() => record(cleanupSummary.value.checkpoint))
const userMemorySummary = computed(() => record(cleanupSummary.value.user_memory))
const cleanupExpiredProcessing = computed(() =>
  Number(checkpointSummary.value.expired_processing || 0)
  + Number(userMemorySummary.value.expired_processing || 0)
)
const cleanupItems = computed<Record<string, unknown>[]>(() => {
  const rows = cleanupOutbox.value.items
  return Array.isArray(rows) ? rows.filter(isObject) : []
})
const policy = computed(() => record(dashboard.value?.refresh_policy))

const cleanupWorkerStatus = computed(() => {
  if (!dashboard.value || !cleanupRuntime.value.observed_at) return '未知'
  if (cleanupRuntime.value.is_stale) {
    return cleanupExpiredProcessing.value > 0 ? '异常' : '失联'
  }
  if (cleanupRuntime.value.last_error_present || Number(cleanupRuntime.value.run_failure_count || 0) > 0) {
    return '警告'
  }
  if (cleanupRuntime.value.worker_status === 'stopped') return '已停止'
  if (cleanupRuntime.value.worker_status === 'unknown') return '未知'
  return cleanupRuntime.value.worker_status === 'processing' ? '处理中' : '空闲'
})

// 兼容旧版 mock/过渡接口；真实共享快照包含 cleanup 字段时不再在数据库状态页展示 Job。
const legacyJobsVisible = computed(() => Boolean(
  dashboard.value
  && !Object.prototype.hasOwnProperty.call(dashboard.value, 'agent_persistence_cleanup_runtime')
))

/** 将当前三段视图写入 URL，保留从用户删除结果带入的 operation_id。 */
function selectView(view: DashboardView): void {
  void router.push({
    path: '/database',
    query: {
      view,
      ...(operationId.value ? { operation_id: operationId.value } : {}),
    },
  })
}

/** 为从用户删除结果带入的 Operation ID 标记对应 outbox 行。 */
function outboxRowClassName({ row }: { row: Record<string, unknown> }): string {
  return row.operation_id === operationId.value ? 'operation-highlight' : ''
}

const revisionMetric = computed(() => capacity.value.revision)
const revision = computed(() => record(metricValue(revisionMetric.value)))
const revisionMeta = computed(() => dashboard.value
  ? metricResult(revisionMetric.value, capacity.value)
  : failureMeta.value)
const revisionValue = computed(() => revision.value.matches === true
  ? '一致'
  : revision.value.matches === false ? '不一致' : '未知')
const revisionDetail = computed(() => {
  const repository = Array.isArray(revision.value.repository_heads) ? revision.value.repository_heads.join(', ') : ''
  const instance = Array.isArray(revision.value.instance_revisions) ? revision.value.instance_revisions.join(', ') : ''
  return `仓库 ${displayValue(repository)} · 实例 ${displayValue(instance)}`
})

const primaryMetric = computed(() => realtime.value.primary)
const primary = computed(() => record(metricValue(primaryMetric.value)))
const primaryMeta = computed(() => dashboard.value
  ? metricResult(primaryMetric.value, realtime.value)
  : failureMeta.value)
const primaryValue = computed(() => primary.value.connected === true
  ? '已连接'
  : primary.value.connected === false ? '不可用' : statusLabel(displayStatus(primaryMeta.value)))
const primaryDetail = computed(() => primary.value.version
  ? `MySQL ${primary.value.version}`
  : String(primaryMeta.value.warning || '暂无补充信息'))

const replicaMetric = computed(() => realtime.value.replica)
const replica = computed(() => record(metricValue(replicaMetric.value)))
const replicaMeta = computed(() => dashboard.value
  ? metricResult(replicaMetric.value, realtime.value)
  : failureMeta.value)
const replicaValue = computed(() => replica.value.configured === false
  ? '未配置'
  : replica.value.available === true
    ? `${displayValue(replica.value.lag_seconds, '?')} 秒延迟`
    : replica.value.available === false ? '不可用' : statusLabel(displayStatus(replicaMeta.value)))
const replicaDetail = computed(() => replica.value.available
  ? String(replica.value.last_io_error || replica.value.last_sql_error
      || `IO ${displayValue(replica.value.io_running)} · SQL ${displayValue(replica.value.sql_running)}`)
  : String(replicaMeta.value.warning || '暂无补充信息'))

const connectionsMetric = computed(() => realtime.value.connections)
const connections = computed(() => record(metricValue(connectionsMetric.value)))
const connectionsMeta = computed(() => dashboard.value
  ? metricResult(connectionsMetric.value, realtime.value)
  : failureMeta.value)
const connectionsValue = computed(() => (
  connections.value.utilization_percent === null
  || connections.value.utilization_percent === undefined
) ? '未知' : `${connections.value.utilization_percent}%`)
const connectionsDetail = computed(() =>
  `${formatNumber(connections.value.threads_connected)} / ${formatNumber(connections.value.max_connections)} 连接`
  + ` · Running ${formatNumber(connections.value.threads_running)}`
  + ` · 历史峰值 ${formatNumber(connections.value.max_used_connections)}`)

const blocking = computed(() => dashboard.value
  ? deriveBlockingCard(dashboard.value)
  : { status: 'unknown' as const, value: 0, detail: failureMeta.value.warning as string, meta: failureMeta.value })

const tablesMetric = computed(() => capacity.value.tables)
const tables = computed<Record<string, unknown>[]>(() => {
  const value = metricValue(tablesMetric.value)
  return Array.isArray(value) ? value.filter(isObject) : []
})
const tablesMeta = computed(() => dashboard.value
  ? metricResult(tablesMetric.value, capacity.value)
  : failureMeta.value)

const integrityChecks = computed<Record<string, unknown>[]>(() =>
  Array.isArray(integrity.value.checks) ? integrity.value.checks.filter(isObject) : [])

const statements = computed<Record<string, unknown>[]>(() => {
  const value = sqlPerformance.value.high_load_statements || sqlPerformance.value.top_statements
  return Array.isArray(value) ? value.filter(isObject) : []
})

const jobsMetric = computed(() => realtime.value.jobs)
const jobs = computed(() => record(metricValue(jobsMetric.value)))
const jobsSummary = computed(() => record(jobs.value.summary || jobs.value))
const jobRows = computed<Record<string, unknown>[]>(() => {
  const rows = jobs.value.data || jobs.value.jobs || jobs.value.active_jobs
  return Array.isArray(rows) ? rows.filter(isObject) : []
})
const jobsMeta = computed(() => dashboard.value
  ? metricResult(jobs.value.meta || jobsMetric.value, realtime.value)
  : failureMeta.value)

const lastObservedAt = computed(() => {
  const times = [
    realtime.value.observed_at,
    sqlPerformance.value.observed_at,
    capacity.value.observed_at,
    integrity.value.observed_at,
    cleanupRuntime.value.observed_at,
    cleanupOutbox.value.observed_at,
  ].filter(Boolean).map(value => new Date(String(value)).getTime()).filter(Number.isFinite)
  return times.length ? `最后采集：${formatDate(Math.max(...times))}` : '尚未生成监控快照'
})

const refreshPolicyText = computed(() => {
  if (policy.value.auto_refresh_enabled !== true) return '自动刷新已关闭 · 可手动刷新共享快照'
  const realtimeSeconds = Number(policy.value.realtime_interval_seconds)
  const sqlSeconds = Number(policy.value.sql_interval_seconds)
  const capacitySeconds = Number(policy.value.table_capacity_interval_seconds)
  return [realtimeSeconds, sqlSeconds, capacitySeconds].every(value => Number.isFinite(value) && value > 0)
    ? `自动读取 ${realtimeSeconds} 秒 · SQL ${sqlSeconds} 秒 · 容量 ${capacitySeconds} 秒`
    : '自动刷新策略不可用 · 可手动刷新共享快照'
})

/** 根据容量分层状态生成表容量的排队、过期、失败与空态提示。 */
function tableState(): { message: string; tone: 'warning' | 'error' | '' } {
  if (!dashboard.value) return { message: failureMeta.value.warning as string, tone: 'error' }
  if (capacity.value.refresh_pending) return { message: '表容量刷新请求已登记，正在等待 monitor 完成采集。', tone: 'warning' }
  if (capacity.value.is_stale) return { message: '表容量快照已过期，当前展示的是最近一次可用结果。', tone: 'warning' }
  if (capacity.value.warning || tablesMeta.value.warning) {
    return {
      message: String(capacity.value.warning || tablesMeta.value.warning),
      tone: displayStatus(capacity.value) === 'error' ? 'error' : 'warning',
    }
  }
  if (!tables.value.length) {
    return { message: capacity.value.observed_at ? '当前数据库没有可展示的表容量数据。' : '表容量快照尚未生成。', tone: '' }
  }
  return { message: '', tone: '' }
}

/** 根据完整性分层和定时策略生成审计状态提示。 */
function integrityState(): { message: string; tone: 'warning' | 'error' | '' } {
  if (!dashboard.value) return { message: failureMeta.value.warning as string, tone: 'error' }
  if (integrity.value.refresh_pending) return { message: '完整性审计请求已登记，正在等待 monitor 执行。', tone: 'warning' }
  if (!integrity.value.observed_at) {
    return {
      message: policy.value.integrity_enabled
        ? '完整性审计尚未执行，monitor 将按低频策略采集；也可立即手动执行。'
        : '完整性定时审计已关闭，尚无审计结果；可点击“执行完整性审计”。',
      tone: 'warning',
    }
  }
  if (integrity.value.is_stale) return { message: '完整性审计快照已过期，当前结果仅供参考；可手动重新执行。', tone: 'warning' }
  if (integrity.value.warning) {
    return { message: String(integrity.value.warning), tone: displayStatus(integrity.value) === 'error' ? 'error' : 'warning' }
  }
  if (!integrityChecks.value.length) return { message: '最近一次审计没有返回可展示的检查项。', tone: '' }
  if (!policy.value.integrity_enabled) return { message: '完整性定时审计已关闭；当前展示最近一次手动或迁移后审计结果。', tone: 'warning' }
  return { message: '', tone: '' }
}

/** 根据 SQL 快照生成排队、无快照、过期、降级和空态提示。 */
function sqlState(): { message: string; tone: 'warning' | 'error' | '' } {
  if (!dashboard.value) return { message: failureMeta.value.warning as string, tone: 'error' }
  if (sqlPerformance.value.refresh_pending) return { message: 'SQL 性能刷新请求已登记，正在等待 monitor 完成采集。', tone: 'warning' }
  if (!sqlPerformance.value.observed_at) return { message: 'SQL 性能快照尚未生成。', tone: 'warning' }
  if (sqlPerformance.value.is_stale) return { message: 'SQL 性能快照已过期，当前展示的是最近一次可用结果。', tone: 'warning' }
  if (sqlPerformance.value.warning) {
    return { message: String(sqlPerformance.value.warning), tone: displayStatus(sqlPerformance.value) === 'error' ? 'error' : 'warning' }
  }
  if (!statements.value.length) return { message: 'Performance Schema 当前没有可展示的高负载 SQL digest。', tone: '' }
  return { message: '', tone: '' }
}

/** 根据 realtime 与任务元数据生成 Worker/Job 状态提示。 */
function jobsState(): { message: string; tone: 'warning' | 'error' | '' } {
  if (!dashboard.value) return { message: failureMeta.value.warning as string, tone: 'error' }
  if (realtime.value.refresh_pending) return { message: '实时状态刷新请求已登记，正在等待 monitor 完成采集。', tone: 'warning' }
  if (realtime.value.is_stale) return { message: 'Worker / Job 快照已过期，monitor 可能未正常运行。', tone: 'warning' }
  if (realtime.value.warning || jobsMeta.value.warning) {
    return {
      message: String(realtime.value.warning || jobsMeta.value.warning),
      tone: displayStatus(realtime.value) === 'error' ? 'error' : 'warning',
    }
  }
  if (!jobRows.value.length) return { message: realtime.value.observed_at ? '当前没有 queued/running 任务。' : '实时状态快照尚未生成。', tone: '' }
  return { message: '', tone: '' }
}

/** 清理当前页面的自动读取定时器。 */
function clearAutoRefresh(): void {
  if (autoRefreshTimer !== undefined) window.clearTimeout(autoRefreshTimer)
  autoRefreshTimer = undefined
}

/** 清理成功提示的自动隐藏定时器。 */
function clearNoticeTimer(): void {
  if (noticeTimer !== undefined) window.clearTimeout(noticeTimer)
  noticeTimer = undefined
}

/** 仅让当前成功提示在固定时间后消失，不覆盖后续警告或错误。 */
function scheduleNoticeDismiss(): void {
  const currentNotice = pageNotice.value
  clearNoticeTimer()
  noticeTimer = window.setTimeout(() => {
    if (pageNotice.value === currentNotice) pageNotice.value = ''
    noticeTimer = undefined
  }, SUCCESS_NOTICE_DURATION_MS)
}

/** 按服务端有效 realtime 周期安排下一次聚合快照读取。 */
function scheduleAutoRefresh(): void {
  clearAutoRefresh()
  if (policy.value.auto_refresh_enabled !== true) return
  const seconds = Number(policy.value.realtime_interval_seconds)
  if (!Number.isFinite(seconds) || seconds <= 0) return
  autoRefreshTimer = window.setTimeout(() => {
    void loadDashboard(true)
  }, seconds * 1000)
}

/** 去重读取聚合快照，并统一维护加载、失败与下一轮调度。 */
async function loadDashboard(quiet = false): Promise<DashboardData> {
  if (dashboardRequest) return dashboardRequest
  dashboardRequest = adminApi.dashboard()
    .then(data => {
      dashboard.value = data
      if (!quiet) {
        clearNoticeTimer()
        pageNotice.value = ''
      }
      return data
    })
    .catch(error => {
      pageNotice.value = `数据库看板加载失败：${error instanceof Error ? error.message : String(error)}`
      noticeType.value = 'error'
      throw error
    })
    .finally(() => {
      dashboardRequest = null
      loading.value = false
      scheduleAutoRefresh()
    })
  return dashboardRequest
}

/** 以 1.5 秒间隔轮询，最长 60 秒等待指定分层完成请求。 */
async function pollUntilSettled(keys: readonly string[], requestedAt: string): Promise<boolean> {
  const deadline = Date.now() + MANUAL_REFRESH_TIMEOUT_MS
  while (Date.now() < deadline) {
    const data = await loadDashboard(true)
    if (keys.every(key => snapshotObservedRequest(data[key as keyof DashboardData] as SnapshotMeta, requestedAt))) return true
    await new Promise(resolve => window.setTimeout(resolve, MANUAL_POLL_INTERVAL_MS))
  }
  return false
}

/** 登记 realtime、SQL、capacity 共享刷新并等待分层完成。 */
async function requestSharedRefresh(): Promise<void> {
  clearAutoRefresh()
  clearNoticeTimer()
  refreshing.value = true
  try {
    const response = await adminApi.refresh()
    pageNotice.value = '普通刷新请求已登记，正在等待 realtime、SQL 性能和表容量快照更新。'
    noticeType.value = 'info'
    const completed = await pollUntilSettled(REFRESH_GROUP_KEYS, response.requested_at)
    pageNotice.value = completed ? '共享监控快照已刷新。' : '刷新仍在后台进行，请确认 monitor 是否正常运行。'
    noticeType.value = completed ? 'info' : 'warning'
    if (completed) scheduleNoticeDismiss()
  } catch (error) {
    pageNotice.value = `手动刷新失败：${error instanceof Error ? error.message : String(error)}`
    noticeType.value = 'error'
  } finally {
    refreshing.value = false
    scheduleAutoRefresh()
  }
}

/** 单独登记完整性审计请求并等待 integrity 分层完成。 */
async function requestIntegrityAudit(): Promise<void> {
  clearAutoRefresh()
  clearNoticeTimer()
  auditing.value = true
  try {
    const response = await adminApi.runIntegrity()
    pageNotice.value = '完整性审计请求已登记，正在等待 monitor 完成审计。'
    noticeType.value = 'info'
    const completed = await pollUntilSettled(['integrity'], response.requested_at)
    pageNotice.value = completed ? '完整性审计快照已更新。' : '审计仍在后台进行，请确认 monitor 是否正常运行。'
    noticeType.value = completed ? 'info' : 'warning'
    if (completed) scheduleNoticeDismiss()
  } catch (error) {
    pageNotice.value = `完整性审计请求失败：${error instanceof Error ? error.message : String(error)}`
    noticeType.value = 'error'
  } finally {
    auditing.value = false
    scheduleAutoRefresh()
  }
}

onMounted(() => {
  if (!route.query.view) {
    void router.replace({
      path: '/database',
      query: {
        view: 'database',
        ...(operationId.value ? { operation_id: operationId.value } : {}),
      },
    })
  }
  void loadDashboard()
})
onBeforeUnmount(() => {
  clearAutoRefresh()
  clearNoticeTimer()
})
</script>

<template>
  <section class="admin-page database-page">
    <CaPageHeader title="数据库状态看板" :level="1" size="md">
      <template #actions>
      <div class="header-status">
        <span>{{ lastObservedAt }}</span>
        <span>{{ refreshPolicyText }}</span>
      </div>
      <CaButton variant="primary" :loading="refreshing" @click="requestSharedRefresh">手动刷新</CaButton>
      </template>
    </CaPageHeader>

  <CaErrorState
    v-if="pageNotice && noticeType === 'error'"
    class="page-notice"
    title="数据库看板操作失败"
    :description="pageNotice"
  />
  <el-alert
    v-else-if="pageNotice"
    class="page-notice"
    :title="pageNotice"
    :type="noticeType"
    show-icon
    :closable="false"
  />
  <el-alert
    v-if="policy.configuration_state === 'degraded'"
    class="page-notice"
    :title="String(policy.configuration_warning || '在线监控配置读取失败，当前继续展示已有快照')"
    type="warning"
    show-icon
    :closable="false"
  />

  <nav class="dashboard-segmented" aria-label="数据库看板视图">
    <button
      v-for="option in viewOptions"
      :key="option.value"
      type="button"
      class="dashboard-segment"
      :class="{ active: activeView === option.value }"
      :aria-current="activeView === option.value ? 'page' : undefined"
      @click="selectView(option.value)"
    >
      {{ option.label }}
    </button>
  </nav>

  <template v-if="activeView === 'database'">
  <section class="status-grid" aria-label="核心状态">
    <StatusCard label="Revision" :value="revisionValue" :detail="revisionDetail" :meta="revisionMeta" />
    <StatusCard label="主库" :value="primaryValue" :detail="primaryDetail" :meta="primaryMeta" />
    <StatusCard label="第一从库" :value="replicaValue" :detail="replicaDetail" :meta="replicaMeta" />
    <StatusCard label="阻塞项" :value="blocking.value" :detail="blocking.detail" :meta="blocking.meta" />
    <StatusCard label="连接使用率" :value="connectionsValue" :detail="connectionsDetail" :meta="connectionsMeta" />
  </section>

  <CaCard as="section" class="panel" variant="outline" padding="md">
    <div class="panel-header">
      <div>
        <h2>表容量</h2>
      </div>
      <span class="source-meta">{{ metaText(tablesMeta) }}</span>
    </div>
    <div v-if="tableState().message" class="section-state" :class="tableState().tone">{{ tableState().message }}</div>
    <el-table v-if="tables.length" :data="tables" v-loading="loading" table-layout="auto">
      <el-table-column prop="table_name" label="表名" min-width="180" />
      <el-table-column label="估算行数" min-width="120"><template #default="{ row }">{{ formatNumber(row.table_rows) }}</template></el-table-column>
      <el-table-column label="数据量" min-width="120"><template #default="{ row }">{{ formatBytes(row.data_length) }}</template></el-table-column>
      <el-table-column label="索引量" min-width="120"><template #default="{ row }">{{ formatBytes(row.index_length) }}</template></el-table-column>
      <el-table-column label="总大小" min-width="120"><template #default="{ row }">{{ formatBytes(row.total_length) }}</template></el-table-column>
    </el-table>
  </CaCard>

  <CaCard as="section" class="panel" variant="outline" padding="md">
    <div class="panel-header">
      <div>
        <h2>完整性审计</h2>
      </div>
      <div class="panel-actions">
        <span class="source-meta">{{ metaText(integrity) }}</span>
        <CaButton variant="secondary" :loading="auditing" @click="requestIntegrityAudit">执行完整性审计</CaButton>
      </div>
    </div>
    <div v-if="integrityState().message" class="section-state" :class="integrityState().tone">{{ integrityState().message }}</div>
    <el-table v-if="integrityChecks.length" :data="integrityChecks" table-layout="auto">
      <el-table-column label="检查项" min-width="240"><template #default="{ row }">{{ row.label || row.name }}</template></el-table-column>
      <el-table-column label="数量" min-width="100"><template #default="{ row }">{{ formatNumber(row.value) }}</template></el-table-column>
      <el-table-column label="结果" min-width="100">
        <template #default="{ row }">
            <CaBadge :tone="statusTone(row.status)">{{ statusLabel(row.status) }}</CaBadge>
        </template>
      </el-table-column>
      <el-table-column label="来源" min-width="150"><template #default="{ row }">{{ row.source_alias || integrity.source_alias || '共享监控快照' }}</template></el-table-column>
    </el-table>
  </CaCard>

  <CaCard as="section" class="panel" variant="outline" padding="md">
    <div class="panel-header">
      <div>
        <h2>SQL 性能摘要</h2>
      </div>
      <span class="source-meta">
        {{ metaText(sqlPerformance) }}
        <template v-if="sqlPerformance.slow_query_warning_threshold !== null && sqlPerformance.slow_query_warning_threshold !== undefined">
        </template>
      </span>
    </div>
    <div class="inline-metrics five-columns">
      <div><span>slow_query_log</span><strong>{{ displayValue(sqlPerformance.slow_query_log) }}</strong></div>
      <div><span>long_query_time</span><strong>{{ sqlPerformance.long_query_time == null ? '—' : `${sqlPerformance.long_query_time} 秒` }}</strong></div>
      <div class="metric-emphasis"><span>周期内 Slow_queries 增量</span><strong>{{ sqlPerformance.slow_queries_delta == null ? (sqlPerformance.baseline_reset ? '基线重建中' : '—') : formatNumber(sqlPerformance.slow_queries_delta) }}</strong></div>
      <div><span>采集窗口</span><strong>{{ sqlPerformance.window_seconds == null ? '—' : `${formatNumber(sqlPerformance.window_seconds)} 秒` }}</strong></div>
      <div><span>累计 Slow_queries</span><strong>{{ formatNumber(sqlPerformance.slow_queries_total ?? sqlPerformance.Slow_queries) }}</strong></div>
    </div>
    <div v-if="sqlState().message" class="section-state" :class="sqlState().tone">{{ sqlState().message }}</div>
    <template v-if="statements.length">
      <div class="sql-business-heading">
        <strong>高负载 SQL（Digest）</strong>
        <span>点击“查看详情”可核对完整原始摘要字段。</span>
      </div>
      <SqlDigestTable :statements="statements" />
    </template>
  </CaCard>

  <CaCard v-if="legacyJobsVisible" as="section" class="panel" variant="outline" padding="md">
    <div class="panel-header">
      <div>
        <h2>Worker / Job 快照</h2>
        <p>明细最多展示 100 条。</p>
      </div>
      <span class="source-meta">{{ metaText(jobsMeta) }}</span>
    </div>
    <div class="inline-metrics four-columns">
      <div><span>Queued</span><strong>{{ formatNumber(jobsSummary.queued) }}</strong></div>
      <div><span>Running</span><strong>{{ formatNumber(jobsSummary.running) }}</strong></div>
      <div><span>Stale</span><strong>{{ formatNumber(jobsSummary.stale) }}</strong></div>
      <div><span>达到最大尝试仍运行</span><strong>{{ formatNumber(jobsSummary.max_attempts_running) }}</strong></div>
    </div>
    <div v-if="jobsState().message" class="section-state" :class="jobsState().tone">{{ jobsState().message }}</div>
    <el-table v-if="jobRows.length" :data="jobRows" table-layout="auto">
      <el-table-column prop="job_id" label="Job ID" min-width="250" />
      <el-table-column prop="status" label="状态" min-width="100" />
      <el-table-column prop="worker_id" label="Worker" min-width="160" />
      <el-table-column label="尝试次数" min-width="100"><template #default="{ row }">{{ formatNumber(row.attempt_count) }} / {{ formatNumber(row.max_attempts) }}</template></el-table-column>
      <el-table-column label="心跳时间" min-width="180"><template #default="{ row }">{{ formatDate(row.heartbeat_at) }}</template></el-table-column>
      <el-table-column label="创建时间" min-width="180"><template #default="{ row }">{{ formatDate(row.created_at) }}</template></el-table-column>
    </el-table>
  </CaCard>
  </template>

  <template v-else-if="activeView === 'cleanup-worker'">
    <CaCard as="section" class="panel cleanup-worker-panel" variant="outline" padding="md">
      <div class="panel-header">
        <div>
          <h2>PostgreSQL Agent 持久化清理进程</h2>
          <p>该视图只展示清理 worker 最近心跳、当前任务类型和本次启动统计。</p>
        </div>
        <CaBadge :tone="statusTone(cleanupWorkerStatus)">{{ cleanupWorkerStatus }}</CaBadge>
      </div>
      <div class="inline-metrics four-columns">
        <div><span>Worker 逻辑别名</span><strong>{{ cleanupRuntime.worker_alias || 'agent-persistence-cleanup' }}</strong></div>
        <div><span>当前任务类型</span><strong>{{ cleanupTaskTypeLabel(cleanupRuntime.current_task_type) }}</strong></div>
        <div><span>启动时间</span><strong>{{ formatDate(cleanupRuntime.started_at) }}</strong></div>
        <div><span>最近心跳</span><strong>{{ formatDate(cleanupRuntime.heartbeat_at || cleanupRuntime.observed_at) }}</strong></div>
        <div><span>当前处理的 Outbox ID</span><strong>{{ cleanupRuntime.current_outbox_id ?? '—' }}</strong></div>
        <div><span>本次启动成功数量</span><strong>{{ formatNumber(cleanupRuntime.startup_success_count ?? cleanupRuntime.run_success_count) }}</strong></div>
        <div><span>本次启动失败数量</span><strong>{{ formatNumber(cleanupRuntime.startup_failure_count ?? cleanupRuntime.run_failure_count) }}</strong></div>
        <div><span>心跳周期</span><strong>{{ cleanupRuntime.heartbeat_interval_seconds ? `${cleanupRuntime.heartbeat_interval_seconds} 秒` : '—' }}</strong></div>
        <div><span>当前处理耗时</span><strong>{{ (cleanupRuntime.processing_duration_seconds ?? cleanupRuntime.current_processing_duration_seconds) == null ? '—' : `${cleanupRuntime.processing_duration_seconds ?? cleanupRuntime.current_processing_duration_seconds} 秒` }}</strong></div>
      </div>
      <div v-if="!dashboard || !cleanupRuntime.observed_at" class="section-state warning">尚未收到清理 worker 心跳。</div>
      <div v-else-if="cleanupRuntime.is_stale" class="section-state warning">最近心跳已超过阈值，worker 可能失联。</div>
      <div v-else-if="cleanupRuntime.last_error_present" class="section-state warning">最近一次 cleanup 处理出现失败，详细异常保留在服务端日志。</div>
      <div v-else class="section-state">心跳正常，worker 当前{{ cleanupRuntime.worker_status === 'processing' ? '正在处理任务' : '空闲' }}。</div>
    </CaCard>
  </template>

  <template v-else>
    <CaCard as="section" class="panel outbox-panel" variant="outline" padding="md">
      <div class="panel-header">
        <div>
          <h2>Agent 持久化清理 Outbox 队列</h2>
          <p>父子图 checkpoint 与长期记忆 Store 各自汇总；最多展示 100 条待处理或异常记录。</p>
        </div>
        <span class="source-meta">{{ metaText(cleanupOutbox) }}</span>
      </div>
      <el-alert
        v-if="operationId"
        class="page-notice"
        type="info"
        :closable="false"
        :title="`当前关注 Operation ID：${operationId}`"
      />
      <div class="inline-metrics four-columns">
        <div><span>Checkpoint Pending</span><strong>{{ formatNumber(checkpointSummary.pending) }}</strong></div>
        <div><span>Checkpoint 已到执行时间</span><strong>{{ formatNumber(checkpointSummary.due_pending) }}</strong></div>
        <div><span>Checkpoint Processing</span><strong>{{ formatNumber(checkpointSummary.processing) }}</strong></div>
        <div><span>Checkpoint 租约已过期</span><strong>{{ formatNumber(checkpointSummary.expired_processing) }}</strong></div>
        <div><span>Checkpoint Failed</span><strong>{{ formatNumber(checkpointSummary.failed) }}</strong></div>
        <div><span>记忆 Pending</span><strong>{{ formatNumber(userMemorySummary.pending) }}</strong></div>
        <div><span>记忆 已到执行时间</span><strong>{{ formatNumber(userMemorySummary.due_pending) }}</strong></div>
        <div><span>记忆 Processing</span><strong>{{ formatNumber(userMemorySummary.processing) }}</strong></div>
        <div><span>记忆 租约已过期</span><strong>{{ formatNumber(userMemorySummary.expired_processing) }}</strong></div>
        <div><span>记忆 Failed</span><strong>{{ formatNumber(userMemorySummary.failed) }}</strong></div>
        <div><span>最近完成时间</span><strong>{{ formatDate(checkpointSummary.latest_completed_at || userMemorySummary.latest_completed_at) }}</strong></div>
        <div><span>最早待处理时间</span><strong>{{ formatDate(checkpointSummary.earliest_pending_at || userMemorySummary.earliest_pending_at) }}</strong></div>
      </div>
      <div v-if="cleanupOutbox.warning" class="section-state warning">{{ cleanupOutbox.warning }}</div>
      <el-table
        v-if="cleanupItems.length"
        :data="cleanupItems"
        table-layout="auto"
        :row-class-name="outboxRowClassName"
      >
        <el-table-column prop="outbox_id" label="Outbox ID" min-width="100" />
        <el-table-column label="类型" min-width="150">
          <template #default="{ row }">{{ cleanupTaskTypeLabel(row.task_type) }}</template>
        </el-table-column>
        <el-table-column label="清理目标" min-width="230" show-overflow-tooltip>
          <template #default="{ row }">{{ row.thread_id || `用户 ${row.user_id}` }}</template>
        </el-table-column>
        <el-table-column prop="operation_id" label="Operation ID" min-width="230" show-overflow-tooltip />
        <el-table-column label="状态" min-width="120">
          <template #default="{ row }">
            <CaBadge :tone="row.error_state ? 'strong' : statusTone(row.status)">
              {{ row.lease_expired ? '租约过期' : row.status === 'pending' && row.is_due ? '到期 Pending' : row.status }}
            </CaBadge>
          </template>
        </el-table-column>
        <el-table-column label="尝试次数" min-width="100"><template #default="{ row }">{{ row.attempts }} / {{ row.max_attempts }}</template></el-table-column>
        <el-table-column label="可领取时间" min-width="180"><template #default="{ row }">{{ formatDate(row.available_at) }}</template></el-table-column>
        <el-table-column label="租约到期时间" min-width="180"><template #default="{ row }">{{ formatDate(row.lease_expires_at) }}</template></el-table-column>
        <el-table-column label="创建时间" min-width="180"><template #default="{ row }">{{ formatDate(row.created_at) }}</template></el-table-column>
        <el-table-column label="完成时间" min-width="180"><template #default="{ row }">{{ formatDate(row.completed_at) }}</template></el-table-column>
        <el-table-column label="错误" min-width="90"><template #default="{ row }">{{ row.has_error ? '有' : '无' }}</template></el-table-column>
      </el-table>
      <CaEmptyState v-else description="当前没有待处理或异常 cleanup 记录" />
    </CaCard>
  </template>
  </section>
</template>
